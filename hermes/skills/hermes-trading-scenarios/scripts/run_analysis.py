#!/usr/bin/env python3
"""Sole public entry point for TradingAgents runs (absorbed ta.sh, 2026-10-20).

Usage:
  run_analysis.py run TICKER [DATE]          News pipeline + full run (lmstudio xhigh,
                                             3/2 rounds, ~3-4 h)
  run_analysis.py run TICKER [DATE] --quick  Local swift model (swift-1.5-qwen3.8-27b),
                                             low effort, 1/1 round (~30-90 min)
  run_analysis.py news TICKER [DATE]         Build only the news supplement
                                             (news_collect.py: dpa + gnews + de_feeds/Foren, merged)
  run_analysis.py doctor [TICKER] [DATE]     Pre-flight for unattended runs; exit 0 ok,
                                             3 degraded, 4 blocked

Flags for run (all optional; equals form --flag=value, space form is a hard error):
  --quick                Local swift model, low effort, 1/1 round (defaults)
  --no-news              Skip the news pipeline; use an existing supplement if present
  --bg                   Start detached in the background, print log + PID
  --ui                   Run in the full framework TUI in its own gnome-terminal window
                         (not combinable with --bg)
  --llm NAME             Force backend: auto|copilot|lmstudio|webercloud
  --allow-copilot        Enable the fork-only Copilot endpoint for this run
                         (ToS gray zone; also TRADINGAGENTS_ALLOW_COPILOT=1)
  --api-url URL          Generic OpenAI-compatible backend (implies the custom profile);
                         must be combined with --model
  --model M              Model name for --api-url (e.g. unsloth/qwen3.8-27b)
  --portfolio F          JSON with CURRENT holdings + cash (PortfolioContext);
                         add "equity" so the deterministic sizing can derive a size
                         ("risk_per_trade_pct"/"max_position_pct" override the config)
  --news-window-days N   Headline window (news + sentiment + supplement), default 7
  --event-window-days N  First-party event-context window, default 180
  --no-event-context     Do not inject the 180-day first-party block

Pass-through flags (special cases):
  --analysts LIST        Default market,news,fundamentals,social
  --debate-rounds N      Default 3 (--quick: 1) — an explicit value beats --quick
  --risk-rounds N        Default 2 (--quick: 1)
  --timeout N            Default 14400 (seconds)
  --lmstudio-model M     Model pin (A/B runs; also overrides the quick model)
  --weber-model M        Model pin (A/B runs)
  --reasoning-effort X   Reasoning effort for lmstudio (default xhigh, quick: low) and
                         custom (only when set); minimal|low|medium|high|xhigh
  --suffix S             Output dir suffix <DATE>-<S> (A/B runs, retries)
  --supplement FILE      Existing supplement instead of the pipeline
  --dry-run              Show the child command only (no news build, no LLM call)

Advanced:
  --output-dir DIR       Override the output directory (default layout below)
  --lmstudio-url URL     LM Studio base URL (default $LMSTUDIO_BASE_URL, then the profile .env, then http://localhost:1234/v1)
  --weber-url URL        Weber-Cloud base URL (default $WEBERCLOUD_BASE_URL)
  --custom-api-key-env NAME  Env var in the profile .env holding a key for a remote
                         custom endpoint

Ticker metadata (names, isin, sector, Foren, Reddit overrides) comes from
references/hardcoded_sources.yaml — maintain it there. News sources: news_sources/ +
news_collect.py as the only news entry point; `news_collect.py sources` shows feed status.

Environment (overridable): TRADINGAGENTS_HOME (default ~/hermes/workspace/Home/TradingAgents).
The script re-execs itself under the fork's venv python when started with a foreign
interpreter, so plain `python3 run_analysis.py ...` works from anywhere.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, re, shlex, shutil, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlparse
import ipaddress
from dotenv import dotenv_values

NEWS_SUPPLEMENT_MAX_ITEMS = 30    # bounded evidence snapshot, matches the news_supplement vendor
NEWS_SUPPLEMENT_MAX_BYTES = 100_000

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,19}$", re.ASCII)
ALLOWED_ANALYSTS = {"market", "news", "fundamentals", "social"}
REPORT_SUFFIXES = {".md", ".html", ".json", ".txt"}

# Public flag surface is equals-form only (--flag=value); a bare value flag on the
# command line is a hard error (carried over from ta.sh, now enforced in one place).
VALUE_FLAGS = {"--llm", "--api-url", "--model", "--portfolio", "--analysts",
               "--debate-rounds", "--risk-rounds", "--timeout", "--lmstudio-model",
               "--weber-model", "--reasoning-effort", "--suffix", "--supplement",
               "--custom-api-key-env", "--output-dir", "--lmstudio-url", "--weber-url",
               "--news-window-days", "--event-window-days"}

# --quick profile: the local swift model with a minimal thinking budget, 1/1 rounds.
QUICK_LLM_MODEL = os.environ.get("TA_QUICK_MODEL") or "swift-1.5-qwen3.8-27b"
QUICK_REASONING_EFFORT = "low"

WRAPPER_VERSION = "2.7.0"


def fail(message: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    raise SystemExit(code)

def iso_date(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD")

def ticker(value: str) -> str:
    value = value.strip().upper()
    if not TICKER_RE.fullmatch(value) or ".." in value:
        raise argparse.ArgumentTypeError("invalid ticker")
    return value

def analysts(value: str) -> str:
    items = [x.strip().lower() for x in value.split(",") if x.strip()]
    if not items or len(items) != len(set(items)) or set(items) - ALLOWED_ANALYSTS:
        raise argparse.ArgumentTypeError("analysts must be unique values from market,news,fundamentals,social")
    return ",".join(items)

def rounds(value: str) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("rounds must be an integer")
    if not 1 <= n <= 5:
        raise argparse.ArgumentTypeError("rounds must be between 1 and 5 (recursion-limit budget)")
    return n

def suffix_value(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise argparse.ArgumentTypeError("suffix allows only a-z, 0-9, _ or -")
    return value

def supplement(value: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"news supplement file not found: {value}")
    if path.stat().st_size > NEWS_SUPPLEMENT_MAX_BYTES:
        raise argparse.ArgumentTypeError("news supplement must be at most 100 KB")
    return str(path)

def portfolio_file(value: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"portfolio file not found: {value}")
    try:
        import json as _json
        _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise argparse.ArgumentTypeError(f"portfolio file is not valid JSON: {exc}")
    return str(path)

def validate_supplement(path: str, ticker: str, as_of: str) -> None:
    """Light structural pre-check before spending a full run on it.

    The repo's news_supplement vendor performs the authoritative validation at
    fetch time (schema v1, public https URLs, timestamp sanity); this only
    catches the cheap, obvious mismatches early with a clear message.
    """
    try:
        data = json.loads(Path(path).read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"news supplement is not valid JSON: {exc}")
    if data.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if data.get("ticker") != ticker:
        raise ValueError(f"ticker must be {ticker}, got {data.get('ticker')!r}")
    if data.get("as_of") != as_of:
        raise ValueError(f"as_of must equal the run date {as_of}, got {data.get('as_of')!r}")
    items = data.get("items")
    if items is None:
        items = []
    if not isinstance(items, list) or len(items) > NEWS_SUPPLEMENT_MAX_ITEMS:
        raise ValueError(
            f"items must be a list of at most {NEWS_SUPPLEMENT_MAX_ITEMS} entries")
    facts = data.get("facts")
    if facts is not None:
        # Facts are current-state figures with their own reference date; they
        # are deliberately NOT bound to the headline window (see the repo's
        # news_supplement vendor). Validate the shape, not the date.
        if not isinstance(facts, dict):
            raise ValueError("facts must be an object")
        if not str(facts.get("reference_date", "")).strip():
            raise ValueError("facts.reference_date is required")
        figures = facts.get("figures")
        if not isinstance(figures, list) or len(figures) > 40:
            raise ValueError("facts.figures must be a list of at most 40 entries")
        for i, figure in enumerate(figures):
            if not isinstance(figure, dict):
                raise ValueError(f"facts.figures[{i}] must be an object")
            if not str(figure.get("label", "")).strip() or not str(figure.get("value", "")).strip():
                raise ValueError(f"facts.figures[{i}] needs label and value")
            if not str(figure.get("source_url", "")).startswith("https://"):
                raise ValueError(f"facts.figures[{i}].source_url must be a public https URL")
    if not items and not facts:
        raise ValueError("supplement needs items and/or a facts block")
    for i, item in enumerate(items):
        if not str(item.get("url", "")).startswith("https://"):
            raise ValueError(f"items[{i}].url must be a public https URL")
        for key in ("title", "source", "url", "published_at", "retrieved_at", "excerpt"):
            if not str(item.get(key, "")).strip():
                raise ValueError(f"items[{i}].{key} is missing or empty")

def safe_dir(path: str) -> Path:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise argparse.ArgumentTypeError("output directory must not be a symlink")
    p = raw.resolve()
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def prompt_versions(root: Path) -> dict[str, str]:
    """sha256 of every prompt-bearing module in the installed checkout.

    The agents' prompts live in ``tradingagents/agents/``. Hashing those files at
    run start turns "which prompts produced this report?" into a fact in
    ``provenance.json`` instead of an assumption about the checkout state.
    """
    base = root / "tradingagents" / "agents"
    if not base.is_dir():
        return {}
    return {str(f.relative_to(root)): sha256(f)
            for f in sorted(base.rglob("*.py")) if "__pycache__" not in f.parts}


def effective_effort(env: dict[str, str]) -> str:
    """The reasoning effort the child actually receives (``provider-default`` if unset).

    Recorded with the run: two runs that differ only in effort were
    indistinguishable in provenance and in the replay id, which made "which run
    was xhigh?" unanswerable after the fact.
    """
    value = (env.get("TRADINGAGENTS_OPENAI_REASONING_EFFORT") or "").strip()
    return value or "provider-default"


def replay_id(a, profile: str, prompts: dict[str, str], effort: str = "provider-default") -> str:
    """A short digest of everything that decides what this run should produce.

    Inputs + rounds + backend/model + reasoning effort + prompt hashes: two runs
    with the same id made the same decisions from the same evidence, which is
    what makes a report auditable and a re-run reproducible.
    """
    parts = {
        "ticker": a.ticker, "date": a.date,
        "analysts": a.analysts.split(",") if isinstance(a.analysts, str) else list(a.analysts),
        "debate_rounds": a.debate_rounds, "risk_rounds": a.risk_rounds,
        "llm_profile": profile, "reasoning_effort": effort, "prompts": prompts,
    }
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def find_install_root() -> Path:
    configured = os.environ.get("TRADINGAGENTS_HOME", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend([
        Path.cwd() / "TradingAgents",
        Path.cwd(),
        Path.home() / "hermes" / "workspace" / "Home" / "TradingAgents",
    ])
    for candidate in candidates:
        root = candidate.resolve()
        if (root / ".venv" / "bin" / "tradingagents").is_file() or (root / "cli" / "main.py").is_file():
            return root
    fail("TradingAgents not found. Set TRADINGAGENTS_HOME to its installation directory.")
    raise AssertionError("unreachable")


def discover_command(root: Path) -> list[str]:
    venv_cli = root / ".venv" / "bin" / "tradingagents"
    if venv_cli.is_file():
        return [str(venv_cli)]
    exe = shutil.which("tradingagents")
    if exe:
        return [exe]
    cli = root / "cli" / "main.py"
    if cli.is_file():
        return [sys.executable, str(cli)]
    fail("TradingAgents CLI executable not found in the installation directory.")
    return []


def _endpoint_reachable(url: str, timeout: int = 8, headers: dict | None = None) -> bool:
    """Fast reachability probe of an OpenAI-compatible /models endpoint.

    Any HTTP response (200, 401, 403, ...) means the server is up — only a
    connection-level failure (DNS, timeout, no route) counts as unreachable.
    Weber-Cloud's /models returns 401 without a key, which is still "reachable".
    """
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(url.rstrip("/") + "/models", headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(256)
            return True
    except urllib.error.HTTPError:
        return True  # server answered (e.g. 401 unauth) => reachable
    except Exception:
        return False


def _lmstudio_url() -> str:
    """LM Studio base URL: environment → profile .env → localhost default.

    Hermes does not export the profile .env into the shell, so both
    LMSTUDIO_BASE_URL and the older LM_BASE_URL are read from the file as well.
    The localhost fallback keeps this script free of any machine-specific host.
    """
    for key in ("LMSTUDIO_BASE_URL", "LM_BASE_URL"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    profile_values = dotenv_values(Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser() / ".env")
    for key in ("LMSTUDIO_BASE_URL", "LM_BASE_URL"):
        value = (profile_values.get(key) or "").strip()
        if value:
            return value
    return "http://localhost:1234/v1"

def _lmstudio_model() -> str:
    return os.environ.get("LMSTUDIO_MODEL") or "unsloth/qwen3.8-27b"

def _check_lmstudio_reachable(url: str) -> None:
    if not _endpoint_reachable(url):
        fail(f"LM Studio endpoint {url} is not reachable: "
             "start the server or point LMSTUDIO_BASE_URL at a running endpoint.")

def _weber_key() -> str:
    profile_values = dotenv_values(Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser() / ".env")
    return (profile_values.get("HERMES_CUSTOM_WEBER_CLOUD_API_KEY")
            or os.environ.get("HERMES_CUSTOM_WEBER_CLOUD_API_KEY") or "").strip()

def _weber_url() -> str:
    return os.environ.get("WEBERCLOUD_BASE_URL") or "https://api.webercloud.ai/v1"

def _weber_model() -> str:
    return os.environ.get("WEBERCLOUD_MODEL") or "qwen/qwen3.8-27b-medium"

def _custom_url() -> str:
    return (os.environ.get("CUSTOM_LLM_BASE_URL") or "").strip()

def _custom_model() -> str:
    return (os.environ.get("CUSTOM_LLM_MODEL") or "").strip()

def _is_local_host(host: str) -> bool:
    """Loopback/private IP literals and localhost. LAN hostnames are not —
    they still work with a dummy key, the caller just warns."""
    if host in ("localhost", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private

def _check_weber_reachable(url: str) -> None:
    if not _endpoint_reachable(url):
        fail(f"Weber-Cloud endpoint {url} is not reachable.")


# Fallback order for --llm auto: primary cloud, local, then the Weber-Cloud
# mirror. Each is tried in turn when the previous one fails on an LLM-backend
# error (quota / host down / timeout-with-no-report).
LLM_CHAIN = ["copilot", "lmstudio", "webercloud"]
# Copilot is a fork-only, ToS-gray endpoint (VSCode client headers) — never a
# silent default: it must be enabled explicitly, per run (--allow-copilot) or
# per environment (TRADINGAGENTS_ALLOW_COPILOT=1).
ALLOW_COPILOT_ENV = "TRADINGAGENTS_ALLOW_COPILOT"
# Analysis horizons: the repo reads these from the environment (see
# _ENV_OVERRIDES in default_config.py), so the wrapper only has to set them.
NEWS_WINDOW_ENV = "TRADINGAGENTS_NEWS_LOOKBACK_DAYS"
EVENT_WINDOW_ENV = "TRADINGAGENTS_EVENT_LOOKBACK_DAYS"
EVENT_CONTEXT_ENV = "TRADINGAGENTS_EVENT_CONTEXT"
COPILOT_TOS_NOTE = (
    "fork-only endpoint: the Copilot backend uses VSCode client headers against "
    "api.githubcopilot.com (ToS gray zone) — enabled explicitly for this run"
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _copilot_allowed() -> bool:
    return _truthy(os.environ.get(ALLOW_COPILOT_ENV))


def _to_text(x) -> str:
    """Normalize subprocess output to str.

    subprocess can hand back bytes even with text=True (notably partial
    output on TimeoutExpired); log assembly and provenance must never
    crash on str + bytes in the failure path.
    """
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode("utf-8", "replace")
    return x


def _copilot_token() -> str:
    profile_values = dotenv_values(Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser() / ".env")
    return (profile_values.get("COPILOT_GITHUB_TOKEN") or os.environ.get("COPILOT_GITHUB_TOKEN") or "").strip()


def _profile_precheck(name: str) -> tuple[bool, str]:
    """Non-failing availability check for one profile (used by the auto chain)."""
    if name == "copilot":
        if not _copilot_allowed():
            return False, (f"disabled by feature flag — set {ALLOW_COPILOT_ENV}=1 "
                           f"or pass --allow-copilot (fork-only endpoint, ToS gray zone)")
        ok = bool(_copilot_token())
        return ok, "copilot token present" if ok else "no COPILOT_GITHUB_TOKEN"
    if name == "lmstudio":
        url = _lmstudio_url()
        ok = _endpoint_reachable(url)
        return ok, (f"lmstudio reachable ({url})" if ok else f"lmstudio unreachable ({url})")
    if name == "webercloud":
        ok = bool(_weber_key()) and _endpoint_reachable(_weber_url())
        return ok, ("webercloud ready" if ok else "webercloud key/endpoint missing")
    if name == "custom":
        url, model = _custom_url(), _custom_model()
        if not url or not model:
            return False, "custom profile needs --api-url and --model"
        ok = _endpoint_reachable(url)
        return ok, (f"custom endpoint reachable ({url})" if ok else f"custom endpoint unreachable ({url})")
    return False, f"unknown profile {name}"


def _resolve_profile(name: str) -> dict[str, str]:
    """Env defaults for one concrete profile (no reachability gating)."""
    if name == "copilot":
        return {
            "TRADINGAGENTS_LLM_PROVIDER": "openai",
            "TRADINGAGENTS_QUICK_THINK_PROVIDER": "openai",
            "TRADINGAGENTS_DEEP_THINK_PROVIDER": "openai",
            "TRADINGAGENTS_QUICK_THINK_LLM": "gpt-6-luna",
            "TRADINGAGENTS_DEEP_THINK_LLM": "gpt-6-luna",
            "TRADINGAGENTS_LLM_BACKEND_URL": "https://api.githubcopilot.com",
            "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "high",
        }
    if name == "lmstudio":
        # unsloth/qwen3.8-27b is a thinking model: LM Studio honours
        # reasoning_effort on it (verified — usage returns reasoning_tokens).
        # Effort defaults to high (like the copilot profile) but is overridable
        # via --reasoning-effort / $LMSTUDIO_REASONING_EFFORT.
        return {
            "TRADINGAGENTS_LLM_PROVIDER": "openai_compatible",
            "TRADINGAGENTS_QUICK_THINK_PROVIDER": "openai_compatible",
            "TRADINGAGENTS_DEEP_THINK_PROVIDER": "openai_compatible",
            "TRADINGAGENTS_QUICK_THINK_LLM": _lmstudio_model(),
            "TRADINGAGENTS_DEEP_THINK_LLM": _lmstudio_model(),
            "TRADINGAGENTS_LLM_BACKEND_URL": _lmstudio_url(),
            # xhigh verified accepted by LM Studio (probe 2026-10-15: usage returns reasoning_tokens).
            "TRADINGAGENTS_OPENAI_REASONING_EFFORT": os.environ.get("LMSTUDIO_REASONING_EFFORT") or "high",
            # A single long high-reasoning turn on the local thinking model can
            # exceed the openai SDK's 600s per-request default and abort the run.
            "TRADINGAGENTS_LLM_REQUEST_TIMEOUT": "1800",
        }
    if name == "webercloud":
        # Same 27B model the Hermes chat runs on; the "-medium" variant is a
        # reasoner whose thinking tokens draw against the output budget, so
        # leave max_tokens at its default (unbounded) to keep the visible
        # answer from starving.
        return {
            "TRADINGAGENTS_LLM_PROVIDER": "openai_compatible",
            "TRADINGAGENTS_QUICK_THINK_PROVIDER": "openai_compatible",
            "TRADINGAGENTS_DEEP_THINK_PROVIDER": "openai_compatible",
            "TRADINGAGENTS_QUICK_THINK_LLM": _weber_model(),
            "TRADINGAGENTS_DEEP_THINK_LLM": _weber_model(),
            "TRADINGAGENTS_LLM_BACKEND_URL": _weber_url(),
        }
    if name == "custom":
        url = _custom_url()
        model = _custom_model()
        if not url or not model:
            fail("--api-url and --model must be set together")
        env = {
            "TRADINGAGENTS_LLM_PROVIDER": "openai_compatible",
            "TRADINGAGENTS_QUICK_THINK_PROVIDER": "openai_compatible",
            "TRADINGAGENTS_DEEP_THINK_PROVIDER": "openai_compatible",
            "TRADINGAGENTS_QUICK_THINK_LLM": model,
            "TRADINGAGENTS_DEEP_THINK_LLM": model,
            "TRADINGAGENTS_LLM_BACKEND_URL": url,
        }
        effort = (os.environ.get("CUSTOM_LLM_REASONING_EFFORT") or "").strip()
        if effort:
            env["TRADINGAGENTS_OPENAI_REASONING_EFFORT"] = effort
        # Same as the lmstudio profile: a single long reasoning turn can exceed
        # the openai SDK's 600s per-request default and abort the run.
        env["TRADINGAGENTS_LLM_REQUEST_TIMEOUT"] = "1800"
        return env
    fail(f"Unknown LLM profile: {name} (choose auto, copilot, lmstudio, webercloud or custom)")
    raise AssertionError("unreachable")


def resolve_llm_profile(name: str) -> tuple[str, dict[str, str]]:
    """Resolve one profile, failing loudly if its precondition is missing.

    auto resolves to the first available profile in LLM_CHAIN.
    """
    if name == "auto":
        for cand in LLM_CHAIN:
            ok, reason = _profile_precheck(cand)
            if ok:
                return cand, _resolve_profile(cand)
        fail("No LLM backend is available: copilot needs COPILOT_GITHUB_TOKEN, "
             "lmstudio a reachable LM Studio server, webercloud "
             "HERMES_CUSTOM_WEBER_CLOUD_API_KEY + a reachable endpoint.")
        raise AssertionError("unreachable")
    ok, reason = _profile_precheck(name)
    if not ok:
        fail(f"LLM profile '{name}' is not available: {reason}.")
    return name, _resolve_profile(name)


def build_child_env(out: Path, llm_name: str, llm_defaults: dict[str, str],
                    debate_rounds: int, risk_rounds: int, news_supplement: str | None) -> dict[str, str]:
    """Pass only app/runtime settings and the credentials needed for inference."""
    profile_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    profile_values = dotenv_values(profile_home / ".env")
    copilot_token = (profile_values.get("COPILOT_GITHUB_TOKEN") or os.environ.get("COPILOT_GITHUB_TOKEN") or "").strip()
    alpha_vantage_key = (profile_values.get("ALPHA_VANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY") or "").strip()
    fred_key = (profile_values.get("FRED_API_KEY") or os.environ.get("FRED_API_KEY") or "").strip()
    polygon_key = (profile_values.get("POLYGON_API_KEY") or os.environ.get("POLYGON_API_KEY") or "").strip()
    finnhub_key = (
        profile_values.get("FINNHUB_API_KEY")
        or profile_values.get("FINNHUB_API_TOKEN")
        or os.environ.get("FINNHUB_API_KEY")
        or os.environ.get("FINNHUB_API_TOKEN")
        or ""
    ).strip()
    marketaux_token = (profile_values.get("MARKETAUX_API_TOKEN") or os.environ.get("MARKETAUX_API_TOKEN") or "").strip()
    weber_key = _weber_key()

    # Do not forward Hermes' unrelated credentials (e.g. sudo, LM Studio or OpenRouter).
    pass_names = {
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
        "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "FRED_API_KEY", "ALPHA_VANTAGE_API_KEY", "POLYGON_API_KEY", "FINNHUB_API_KEY",
        "MARKETAUX_API_TOKEN",
    }
    child = {k: v for k, v in os.environ.items() if k in pass_names or k.startswith("TRADINGAGENTS_")}
    child.pop("PYTHONPATH", None)
    child.pop("COPILOT_GITHUB_TOKEN", None)
    child.pop("GH_TOKEN", None)
    child.pop("GITHUB_TOKEN", None)
    if llm_name == "copilot":
        child["OPENAI_API_KEY"] = copilot_token
    if llm_name == "webercloud":
        # openai_compatible provider reads its key from OPENAI_COMPATIBLE_API_KEY.
        child["OPENAI_COMPATIBLE_API_KEY"] = weber_key
    if llm_name == "custom":
        # Generic OpenAI-compatible endpoint: a real key only when the user names an
        # env var holding one; local/LAN endpoints accept any non-empty key.
        url = _custom_url()
        key_env = (os.environ.get("CUSTOM_LLM_API_KEY_ENV") or "").strip()
        if key_env:
            key = (profile_values.get(key_env) or os.environ.get(key_env) or "").strip()
            if not key:
                fail(f"--custom-api-key-env={key_env} is set but {key_env} has no value in the profile .env")
        else:
            host = urlparse(url).hostname or ""
            if not _is_local_host(host):
                print(json.dumps({"warning": f"remote endpoint {host} without --custom-api-key-env — "
                                            f"sending a dummy key; expect 401 if it requires auth"}),
                      file=sys.stderr)
            key = "sk-local"
        child["OPENAI_COMPATIBLE_API_KEY"] = key
    if alpha_vantage_key:
        child["ALPHA_VANTAGE_API_KEY"] = alpha_vantage_key
    if fred_key:
        child["FRED_API_KEY"] = fred_key
    if polygon_key:
        child["POLYGON_API_KEY"] = polygon_key
    if finnhub_key:
        child["FINNHUB_API_KEY"] = finnhub_key
    if marketaux_token:
        child["MARKETAUX_API_TOKEN"] = marketaux_token
    defaults = {
        **llm_defaults,
        "TRADINGAGENTS_OUTPUT_LANGUAGE": "German",
        "TRADINGAGENTS_MAX_DEBATE_ROUNDS": str(debate_rounds),
        "TRADINGAGENTS_MAX_RISK_ROUNDS": str(risk_rounds),
        # ta.sh set this on the child command line; keep it a wrapper default so an
        # explicit TRADINGAGENTS_CHECKPOINT_ENABLED in the environment still wins.
        "TRADINGAGENTS_CHECKPOINT_ENABLED": "true",
    }
    for key, value in defaults.items():
        child.setdefault(key, value)
    if news_supplement:
        # Per-run, manually verified evidence snapshot. The repo's
        # news_supplement vendor consumes it as one more get_news vendor
        # (issuer_rss,yfinance,news_supplement); no graph changes involved.
        child["TRADINGAGENTS_NEWS_SUPPLEMENT"] = news_supplement
    child["TRADINGAGENTS_RESULTS_DIR"] = str(out)
    # out is this run's own directory (ticker/date already in the path), so the
    # framework writes all artifacts directly into it instead of adding layers.
    child["TRADINGAGENTS_RESULTS_LAYOUT"] = "run_dir"
    child["PYTHONUNBUFFERED"] = "1"
    return child


# LLM-backend failures that justify falling back to the next backend in the
# chain. Data-vendor degradations are logged by the router and never crash the
# run, so a crash whose tail matches one of these is an LLM-client fault.
# NOTE: match the *wrapped* exception classes (OpenAIConnectionError,
# APITimeoutError) as well, not just the raw ConnectError — langchain_openai
# re-wraps every underlying connection fault, and the raw signature may sit
# outside the 8000-char tail window when a pretty-printed traceback box
# follows.
_LLM_FAILURE_RE = re.compile(
    r"quota exceeded|OpenAIRateLimitError|No route to host|Connection refused|"
    r"ConnectError|ECONNREFUSED|Name or service not known|AuthenticationError|"
    r"Invalid API key|401 Unauthorized|Failed to authenticate|"
    r"OpenAIConnectionError|OpenAITimeoutError|APIConnectionError|APIError",
    re.IGNORECASE,
)


def _fallback_reason(returncode: int, timed_out: bool, report_files: list, log_text: str):
    """Why this attempt should fall back to the next LLM backend, or None.

    Only LLM-backend faults (quota / host down / auth) and a timeout that
    produced no report count. A crash with no LLM signature is a data/other
    error and must not burn the rest of the chain. A timeout that produced
    report files is real partial progress, not a backend fault.
    """
    if returncode == 0:
        return None
    if timed_out:
        if report_files:
            return None  # real partial progress; keep it, don't fall back
        return "timeout with no report (backend too slow or hung)"
    m = _LLM_FAILURE_RE.search(log_text[-8000:])
    if m:
        return f"LLM backend error: {m.group(0)}"
    return None

# ---------------------------------------------------------------------------
# Data-coverage audit
#
# After a run, the wrapper reads its own tradingagents.log and turns the
# router's *real* vendor-skip markers into a machine-readable gap report that
# goes into provenance.json. This is the automatic, per-run answer to "which
# data gaps does this run have?" for ANY ticker/vendor, not a one-off analysis.
#
# Only log lines the routing layer actually emits are parsed (router.py
# logger.warning calls), so nothing here is invented: a vendor that was
# skipped, and the exact reason, is what the run itself logged. A method with
# no skip marker is reported as "ok" (its primary vendor served it). Success is
# never asserted from the log alone — the absence of a skip marker means "no
# degradation seen", which is the honest reading.
# ---------------------------------------------------------------------------
_COVERAGE_PATTERNS = (
    ("not_configured", re.compile(
        r"Vendor '([^']+)' not configured for (\w+); trying next vendor\.")),
    ("unavailable", re.compile(
        r"Vendor '([^']+)' unavailable for (\w+): (.*?); trying next vendor\.")),
    ("failed", re.compile(
        r"Vendor '([^']+)' failed for (\w+): (.*)$"))
)
_NO_DATA_ERROR_RE = re.compile(
    r"Returning NO_DATA for (\w+), but a vendor errored earlier: (.*)$")
_OPTIONAL_DEGRADED_RE = re.compile(
    r"Optional (\w+) unavailable for (\w+): (.*)$")
_FRED_MISSING_RE = re.compile(r"FRED_API_KEY environment variable is not set")
_MARKETAUX_MISSING_RE = re.compile(r"MARKETAUX_API_TOKEN environment variable is not set")
_FINNHUB_MISSING_RE = re.compile(r"FINNHUB_API_KEY environment variable is not set")


def build_data_coverage(log_text: str) -> dict:
    """Summarise a run's vendor degradation from its log into a gap report.

    Returns ``{"methods": {method: {status, skips, notes}}, "flags": {...}}``.
    ``status`` is ``"ok"`` (no vendor skipped for that method) or
    ``"degraded"`` (one or more configured vendors were skipped/failed; another
    may still have served the call). The precise detail lives in ``skips``.

    This is the log-derived view, kept for runs whose child produced no report
    tree. When the run wrote ``data_coverage.json`` next to its report, that
    matrix (from the routing layer itself) is authoritative — see
    ``read_coverage_matrix``.
    """
    methods: dict[str, dict] = {}
    flags: dict[str, bool] = {
        "fred_api_key_missing": bool(_FRED_MISSING_RE.search(log_text)),
        "marketaux_token_missing": bool(_MARKETAUX_MISSING_RE.search(log_text)),
        "finnhub_token_missing": bool(_FINNHUB_MISSING_RE.search(log_text)),
    }

    def method_record(method: str) -> dict:
        return methods.setdefault(method, {"skips": [], "notes": []})

    if log_text:
        for line in log_text.splitlines():
            for state, rx in _COVERAGE_PATTERNS:
                m = rx.search(line)
                if not m:
                    continue
                vendor, method = m.group(1), m.group(2)
                reason = (m.group(3) or "").strip() if state != "not_configured" else ""
                rec = method_record(method)
                if not any(s["vendor"] == vendor and s["state"] == state for s in rec["skips"]):
                    rec["skips"].append({"vendor": vendor, "state": state, "reason": reason})
            m = _NO_DATA_ERROR_RE.search(line)
            if m:
                method_record(m.group(1))["notes"].append(
                    f"no-data verdict, but a vendor errored earlier: {m.group(2).strip()}")
            m = _OPTIONAL_DEGRADED_RE.search(line)
            if m:
                method_record(m.group(2))["notes"].append(
                    f"optional {m.group(1)} unavailable: {m.group(3).strip()}")

    for rec in methods.values():
        rec["status"] = "degraded" if rec["skips"] else "ok"
    return {"methods": dict(sorted(methods.items())), "flags": flags}


def read_coverage_matrix(out: Path) -> dict | None:
    """The run's own coverage matrix (``data_coverage.json``), newest wins.

    Written by the repo's report writer from the routing layer's records, so it
    exists in both the headless and the ``--ui`` path — unlike the log-derived
    view, which needs captured stdout. Returns ``None`` when the run wrote none.
    """
    candidates = sorted(
        (p for p in out.rglob("data_coverage.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("methods"):
            data["source_path"] = str(path)
            return data
    return None


def report_files(out: Path) -> list[dict]:
    """Every saved report artifact with its size and hash."""
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.suffix.lower() in REPORT_SUFFIXES:
            files.append({"path": str(p), "sha256": sha256(p), "bytes": p.stat().st_size})
    return files


def read_quality_gate(out: Path) -> dict | None:
    """The repo's post-run gate verdict (``quality_gate.json``), newest wins.

    Written by ``save_reports`` into the report tree, so it exists for headless
    and ``--ui`` runs alike. Only the verdict and the failing check names go into
    provenance — the full check list stays in the run directory.
    """
    candidates = sorted(
        (q for q in out.rglob("quality_gate.json") if q.is_file()),
        key=lambda q: q.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            gate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(gate, dict):
            continue
        failed = [c.get("name") for c in gate.get("checks") or []
                  if c.get("blocking") and not c.get("passed")]
        return {
            "status": gate.get("status"),
            "failed_checks": failed,
            "gap_count": len(gate.get("gaps") or []),
            "path": str(path),
        }
    return None


def read_advisory(out: Path) -> dict | None:
    """The run's deterministic sizing verdict (``sizing.json``), newest wins.

    The report tree already carries the full record; the orchestration layer
    only needs to know whether a size was derived and which constraint bound it.
    A missing or corrupt file is "not recorded", never a default.
    """
    candidates = sorted(
        (p for p in out.rglob("sizing.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        position = data.get("position") or {}
        return {
            "verdict": data.get("verdict"),
            "status": data.get("status"),
            "shares": position.get("shares"),
            "binding_constraint": position.get("binding_constraint"),
            "path": str(path),
        }
    return None


def write_provenance(out: Path, payload: dict) -> Path:
    """Write ``provenance.json`` into the run dir (mode 600: it names endpoints)."""
    prov = out / "provenance.json"
    prov.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        prov.chmod(0o600)
    except OSError:
        pass
    return prov



# ---------------------------------------------------------------------------
# Orchestration (absorbed from ta.sh): venv re-exec, ticker metadata, news
# pipeline, output layout, LLM profile defaults, background start.
# ---------------------------------------------------------------------------

def ensure_venv_python() -> None:
    """Re-exec under the fork's venv python when started with a foreign interpreter.

    ta.sh guaranteed this by always invoking $TRADINGAGENTS_HOME/.venv/bin/python;
    without the bash layer the script must keep that guarantee itself (pyyaml and
    friends live in the fork's venv, not necessarily in the caller's python).
    """
    configured = os.environ.get("TRADINGAGENTS_HOME", "").strip()
    home = Path(configured).expanduser() if configured \
        else Path.home() / "hermes" / "workspace" / "Home" / "TradingAgents"
    venv_py = home / ".venv" / "bin" / "python"
    if not venv_py.is_file():
        fail(f"{venv_py} not found (TRADINGAGENTS_HOME?)")
    try:
        same = Path(sys.executable).resolve() == venv_py.resolve()
    except OSError:
        same = False
    if not same:
        os.execv(str(venv_py), [str(venv_py)] + sys.argv)


def load_ticker_meta(ticker: str) -> tuple[list[str], str]:
    """Ticker names + ISIN from references/hardcoded_sources.yaml.

    ta.sh read these via a bash heredoc; in Python it is one yaml.safe_load.
    A ticker without a YAML entry cannot run (the news pipeline needs names).
    """
    import yaml
    path = Path(__file__).resolve().parent.parent / "references" / "hardcoded_sources.yaml"
    if not path.is_file():
        fail(f"hardcoded_sources.yaml missing: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    t = (data.get("tickers") or {}).get(ticker) or {}
    names = [str(n) for n in t.get("names", [])]
    if not names:
        fail(f"Ticker {ticker} not found in {path} (create tickers:{ticker})")
    return names, str(t.get("isin", ""))


def build_news(ticker: str, date: str, names: list[str], isin: str,
               out_dir: Path, news_out: Path) -> bool:
    """Run the news pipeline (only entry point: news_collect.py). True on success.

    Stage logs append to <out_dir>/ta_news.log; one dead source degrades to a log
    line inside news_collect.py and never aborts the pipeline.
    """
    print(f">> News-Pipeline für {ticker} ({date}): dpa + gnews + de_feeds/Foren")
    cmd = [sys.executable, str(Path(__file__).resolve().parent / "news_collect.py"),
           "fetch", "--ticker", ticker, "--date", date, "--names", *names]
    if isin:
        cmd += ["--isin", isin]
    cmd += ["--window-days", "7", "--max", "10", "--out", str(news_out)]
    log = out_dir / "ta_news.log"
    with open(log, "a", encoding="utf-8") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode
    if rc == 0:
        try:
            n = len(json.loads(news_out.read_text(encoding="utf-8"))["items"])
            print(f"   Supplement: {news_out} ({n} Items)")
        except Exception:  # noqa: BLE001 — a broken supplement is reported by the run itself
            print(f"   Supplement: {news_out}")
        return True
    print(f"   News-Pipeline ohne Ergebnis (Details: {log})")
    return False


def default_output_dir(ticker: str, date: str, suffix: str | None) -> Path:
    """~/.tradingagents/reports/trading-scenarios/<TICKER>/<DATE>[-<SUFFIX>] (ta.sh layout)."""
    date_dir = f"{date}-{suffix}" if suffix else date
    return safe_dir(str(Path.home() / ".tradingagents" / "reports" / "trading-scenarios" / ticker / date_dir))


# ---------------------------------------------------------------------------
# doctor: unattended pre-flight
#
# What breaks a long unattended run is almost never the model — it is a missing
# benchmark entry, an invalid registry, an unwritable memory log, a portfolio
# file with the wrong shape. Each is cheap to check and expensive to discover
# after two hours. Exit codes: 0 ok, 3 degraded (runs, with a caveat),
# 4 blocked (do not start).
# ---------------------------------------------------------------------------
DOCTOR_EXIT = {"ok": 0, "degraded": 3, "blocked": 4}

# Facts only the installed package can answer. One child process, one JSON line:
# no import of the repo into this wrapper, no duplicated knowledge that drifts.
_DOCTOR_REPO_CODE = r"""
import json, os
out = {"errors": []}
ticker = os.environ.get("TA_DOCTOR_TICKER") or ""
try:
    from tradingagents.default_config import DEFAULT_CONFIG as cfg
    from tradingagents.memory import TradingMemoryLog, settlement
    if ticker:
        out["benchmark"] = settlement.resolve_benchmark(ticker, cfg)
    out["horizons"] = settlement.get_evaluation_horizons(cfg)
    out["memory_log_path"] = cfg.get("memory_log_path")
    out["issuer_registry_path"] = cfg.get("issuer_registry_path")
    out["benchmark_suffixes"] = sorted(cfg.get("benchmark_map") or {})
    if ticker:
        try:
            from tradingagents.dataflows.vendors.issuer_facts import is_configured
            configured, reason = is_configured(ticker)
            out["ir_facts"] = {"configured": bool(configured), "reason": str(reason)}
        except Exception as exc:
            out["ir_facts"] = {"configured": False, "reason": type(exc).__name__ + ": " + str(exc)}
    log = TradingMemoryLog({"memory_log_path": cfg.get("memory_log_path")})
    entries = log.load_entries()
    out["memory_entries"] = len(entries)
    out["memory_pending"] = sum(1 for e in entries if e.get("pending"))
    out["memory_writable"] = os.access(os.path.dirname(cfg.get("memory_log_path")), os.W_OK)
except Exception as exc:
    out["errors"].append(type(exc).__name__ + ": " + str(exc))
print(json.dumps(out))
"""


def _repo_health(root: Path, ticker: str | None) -> dict:
    """Run the repo's own checks in its venv python; never raise, report instead."""
    venv_py = root / ".venv" / "bin" / "python"
    python = str(venv_py) if venv_py.is_file() else sys.executable
    env = dict(os.environ)
    env["TA_DOCTOR_TICKER"] = ticker or ""
    env.pop("PYTHONPATH", None)
    try:
        cp = subprocess.run([python, "-c", _DOCTOR_REPO_CODE], cwd=str(root), env=env,
                            text=True, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"errors": [f"repo probe failed: {type(exc).__name__}: {exc}"]}
    for line in reversed((cp.stdout or "").strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    detail = (cp.stderr or "").strip().splitlines()[-1:] or ["no output"]
    return {"errors": [f"repo probe produced no JSON ({detail[0]})"]}


def _writable_dir(path: Path) -> tuple[bool, str]:
    """Can a run create this directory (or write into it if it exists)?"""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK), str(probe)


def cmd_doctor(args: argparse.Namespace) -> None:
    """`doctor [TICKER]`: pre-flight for unattended runs. Exit 0/3/4."""
    if getattr(args, "allow_copilot", False):
        os.environ[ALLOW_COPILOT_ENV] = "1"
    checks: list[dict] = []

    def check(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    if getattr(args, "news_window_days", None) or getattr(args, "event_window_days", None):
        apply_window_args(args)
    windows = analysis_windows_summary(run_date(args))
    check("analysis_windows", "ok",
          f"headline {windows['news_lookback_days']}d, event "
          + (f"{windows['event_lookback_days']}d" if windows["event_context_enabled"] else "disabled"))

    # --- installation ------------------------------------------------------
    try:
        root = find_install_root()
    except SystemExit:
        root = None
    if root and root.is_dir():
        check("install_root", "ok", str(root))
    else:
        check("install_root", "blocked", "TradingAgents checkout not found (TRADINGAGENTS_HOME?)")
    if root:
        venv_py = root / ".venv" / "bin" / "python"
        check("venv_python", "ok" if venv_py.is_file() else "blocked",
              str(venv_py) if venv_py.is_file() else f"missing: {venv_py}")
        try:
            check("cli", "ok", " ".join(discover_command(root)))
        except SystemExit as exc:
            check("cli", "blocked", f"no runnable CLI ({exc.code})")

    # --- curated sources / ticker identity -----------------------------------
    sources = Path(__file__).resolve().parent.parent / "references" / "hardcoded_sources.yaml"
    try:
        import yaml
        data = yaml.safe_load(sources.read_text(encoding="utf-8")) or {}
        check("hardcoded_sources", "ok", f"{len(data.get('tickers') or {})} ticker entries")
    except Exception as exc:  # noqa: BLE001 — doctor reports, never raises
        data = {}
        check("hardcoded_sources", "blocked", f"{sources.name} unreadable: {exc}")

    ticker = getattr(args, "ticker", None)
    if ticker:
        meta = (data.get("tickers") or {}).get(ticker)
        if not meta:
            check("ticker_metadata", "blocked", f"{ticker} has no entry in hardcoded_sources.yaml")
        elif not meta.get("names"):
            check("ticker_metadata", "blocked", f"{ticker} entry has no names")
        else:
            isin = str(meta.get("isin") or "")
            check("ticker_metadata", "ok",
                  f"{meta['names'][0]}" + (f", ISIN {isin}" if isin else ", no ISIN (name-only matching)"))

    # --- portfolio / supplement (optional inputs) ---------------------------
    if getattr(args, "portfolio", None):
        try:
            portfolio_file(args.portfolio)
        except argparse.ArgumentTypeError as exc:
            check("portfolio", "blocked", str(exc))
        else:
            check("portfolio", "ok", args.portfolio)
            # Deterministic sizing needs equity; without it the plan is BLOCKED,
            # which an unattended chain should know before spending hours.
            try:
                book = json.loads(Path(args.portfolio).expanduser().read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                book = {}
            equity = book.get("equity") if isinstance(book, dict) else None
            if not isinstance(equity, (int, float)) or equity <= 0:
                check("portfolio_equity", "degraded",
                      "no positive 'equity' -> deterministic sizing will be BLOCKED")
            else:
                check("portfolio_equity", "ok",
                      f"equity {equity:,.2f} {book.get('currency') or ''}".strip())
            # A derivative's exposure needs its ratio from the instrument cache;
            # knowing that before the run is cheaper than a blocked plan after.
            try:
                from tradingagents import instruments as _instruments

                held = [str(p.get("ticker")) for p in book.get("positions") or [] if p.get("ticker")]
                resolved = _instruments.for_book(book)
                missing = [key for key in held if key.upper() not in resolved]
                if missing:
                    check("portfolio_instruments", "degraded",
                          "no term-sheet record for " + ", ".join(missing)
                          + " -> their exposure stays unknown")
                else:
                    check("portfolio_instruments", "ok", f"{len(held)} instrument(s) resolved")
            except Exception as exc:  # noqa: BLE001 -- the doctor must not crash
                check("portfolio_instruments", "degraded", f"{type(exc).__name__}: {exc}"[:120])
    if getattr(args, "supplement", None):
        try:
            supplement(args.supplement)
            validate_supplement(args.supplement, ticker or "", args.date or dt.date.today().isoformat())
            check("news_supplement", "ok", args.supplement)
        except (argparse.ArgumentTypeError, ValueError) as exc:
            check("news_supplement", "blocked", str(exc))

    # --- what the installed package knows -----------------------------------
    health: dict = _repo_health(root, ticker) if root else {"errors": ["no installation"]}
    if health.get("errors"):
        check("repo_probe", "blocked", "; ".join(health["errors"]))
    else:
        check("repo_probe", "ok", "package importable")

    if ticker and health.get("benchmark"):
        check("benchmark", "ok", f"{ticker} -> {health['benchmark']}")
    elif ticker and not health.get("errors"):
        check("benchmark", "degraded", "no benchmark resolved for this suffix (defaults to SPY)")

    horizons = list(health.get("horizons") or [])
    if horizons:
        status = "ok" if len(horizons) > 1 else "degraded"
        check("evaluation_horizons", status,
              ", ".join(str(h) for h in horizons) + (" (legacy single window)" if len(horizons) == 1 else ""))
    elif not health.get("errors"):
        check("evaluation_horizons", "blocked", "no valid evaluation window configured")

    registry_path = health.get("issuer_registry_path")
    if registry_path:
        path = Path(str(registry_path)).expanduser()
        if not path.exists():
            check("issuer_registry", "degraded",
                  f"absent: {path} (ureg_pdmr self-selects only for registered issuers)")
        else:
            try:
                json.loads(path.read_text(encoding="utf-8"))
                check("issuer_registry", "ok", str(path))
            except (OSError, json.JSONDecodeError) as exc:
                check("issuer_registry", "blocked", f"invalid JSON: {exc}")

    # --- IR facts adapter (feeds issue #4: onboarding trigger) ---------------
    if ticker and not health.get("errors"):
        facts_health = health.get("ir_facts") or {}
        if facts_health.get("configured"):
            reason = str(facts_health.get("reason") or "ok")
            check("ir_facts", "ok" if reason == "ok" else "degraded",
                  reason if reason != "ok" else "adapter configured (key figures/guidance/calendar)")
        else:
            check("ir_facts", "degraded",
                  f"{facts_health.get('reason') or 'not configured'} — delegate the "
                  f"ir-facts-onboarding skill to add an ir_facts block, or run with the "
                  f"generic fallback (newest issuer release)")

    if "memory_entries" in health:
        status = "ok" if health.get("memory_writable") else "blocked"
        check("memory_log", status,
              f"{health['memory_entries']} entries ({health.get('memory_pending', 0)} pending); "
              f"path {health.get('memory_log_path')}"
              + ("" if health.get("memory_writable") else " — directory not writable"))

    # --- run directories ----------------------------------------------------
    probe_ticker = ticker or "DOCTOR"
    out_dir = default_output_dir(probe_ticker, args.date or dt.date.today().isoformat(), None)
    ok, existing = _writable_dir(out_dir)
    check("output_dir", "ok" if ok else "blocked", f"{out_dir} (writable: {existing})")
    cache = Path.home() / ".tradingagents" / "cache"
    ok, existing = _writable_dir(cache)
    check("cache_dir", "ok" if ok else "degraded", f"{cache} (writable: {existing})")

    # --- LLM backends -------------------------------------------------------
    llm = getattr(args, "llm", None) or "auto"
    if llm == "auto":
        available = [(name, _profile_precheck(name)) for name in LLM_CHAIN]
        usable = [name for name, (ok, _why) in available if ok]
        detail = "; ".join(f"{n}: {'ok' if ok else why}" for n, (ok, why) in available)
        check("llm_backends", "ok" if usable else "blocked",
              (f"usable: {', '.join(usable)}" if usable else "none reachable") + f" ({detail})")
    else:
        ok, why = _profile_precheck(llm)
        check("llm_backend", "ok" if ok else "blocked", llm if ok else f"{llm}: {why}")

    statuses = {c["status"] for c in checks}
    status = "blocked" if "blocked" in statuses else ("degraded" if "degraded" in statuses else "ok")
    result = {"ok": status == "ok", "status": status, "ticker": ticker,
              "date": args.date or dt.date.today().isoformat(), "checks": checks,
              "exit_code": DOCTOR_EXIT[status]}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(DOCTOR_EXIT[status])


def cmd_news(args: argparse.Namespace) -> None:
    """`news TICKER [DATE]`: build only the supplement, print its path."""
    ticker = args.ticker
    date = args.date or dt.date.today().isoformat()
    names, isin = load_ticker_meta(ticker)
    out = default_output_dir(ticker, date, None)
    news_out = out / f"news_supplement_{ticker}_{date}.json"
    if not build_news(ticker, date, names, isin, out, news_out):
        fail(f"News-Pipeline fehlgeschlagen (Log: {out / 'ta_news.log'})")
    if getattr(args, "facts", False):
        facts_out = out / f"ir_facts_{ticker}_{date}.json"
        facts_cmd = [sys.executable, str(Path(__file__).resolve().parent / "news_collect.py"),
                     "facts", "--ticker", ticker, "--date", date, "--out", str(facts_out)]
        try:
            result = subprocess.run(facts_cmd, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"IR-Fakten-Snapshot fehlgeschlagen: {type(exc).__name__}: {exc}", file=sys.stderr)
        else:
            # A missing adapter is a scope statement, not a broken pipeline: report
            # it and keep the news supplement usable.
            print(str(facts_out) if result.returncode == 0 else
                  f"IR-Fakten nicht verfügbar ({result.stderr.strip()[:200]})", file=sys.stderr)
    print(news_out)


def apply_window_args(a: argparse.Namespace) -> None:
    """Turn --news-window-days/--event-window-days/--no-event-context into env.

    Children (headless, --bg, --ui) inherit the environment, so one place is
    enough; the repo folds these into its config at import time.
    """
    if getattr(a, "news_window_days", None) is not None:
        os.environ[NEWS_WINDOW_ENV] = str(a.news_window_days)
    if getattr(a, "event_window_days", None) is not None:
        os.environ[EVENT_WINDOW_ENV] = str(a.event_window_days)
    if getattr(a, "no_event_context", False):
        os.environ[EVENT_CONTEXT_ENV] = "0"


def run_date(a: argparse.Namespace) -> str:
    """The analysis date of this invocation (today when the flag is absent)."""
    return getattr(a, "date", None) or dt.date.today().isoformat()


def analysis_windows_summary(date_str: str) -> dict:
    """The horizons this run will use, resolved to dates (for provenance)."""
    headline = _env_days(NEWS_WINDOW_ENV, 7)
    event_days = _env_days(EVENT_WINDOW_ENV, 180)
    enabled = (os.environ.get(EVENT_CONTEXT_ENV, "1").strip().lower()
               not in ("0", "false", "no", "off"))
    try:
        end = dt.date.fromisoformat(date_str)
    except ValueError:
        return {"news_lookback_days": headline, "event_lookback_days": event_days,
                "event_context_enabled": enabled}
    return {
        "news_lookback_days": headline,
        "headline_window": [(end - dt.timedelta(days=headline)).isoformat(), end.isoformat()],
        "event_context_enabled": enabled,
        "event_lookback_days": event_days,
        "event_window": [(end - dt.timedelta(days=event_days)).isoformat(), end.isoformat()],
    }


def _env_days(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def positive_days(raw: str) -> int:
    """argparse type for a lookback window in days (1..730)."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"ganze Zahl erwartet, nicht {raw!r}") from None
    if not 1 <= value <= 730:
        raise argparse.ArgumentTypeError("Fenster muss zwischen 1 und 730 Tagen liegen")
    return value


def cmd_run(args: argparse.Namespace) -> None:
    """`run TICKER [DATE]`: validate, build the news supplement, launch the run."""
    if args.bg and args.ui:
        fail("--ui und --bg schließen sich aus (Fenster braucht eine offene Interaktion)")
    # The flag is the per-run consent for a fork-only, ToS-gray endpoint; it is
    # turned into the env var the profile prechecks read, and forwarded to a
    # --bg child (which otherwise loses it).
    if getattr(args, "allow_copilot", False):
        os.environ[ALLOW_COPILOT_ENV] = "1"
    apply_window_args(args)

    ticker = args.ticker  # uppercased + validated by type=ticker
    date = args.date or dt.date.today().isoformat()

    # --- LLM flag validation (ta.sh messages, now in one place) --------------
    if bool(args.api_url) != bool(args.model):
        fail("--api-url und --model müssen zusammen gesetzt werden")
    if args.api_url:
        if not re.fullmatch(r"https?://\S+", args.api_url):
            fail("--api-url: http(s)-URL ohne Leerzeichen erwartet")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", args.model):
            fail("--model: ungültiges Modell-Format (erlaubt: Buchstaben, Ziffern, . _ / -)")
    if args.api_url and args.llm:
        fail("--api-url/--model schließen sich mit --llm aus")

    # --- Output dir + ticker metadata ----------------------------------------
    out = safe_dir(args.output_dir) if args.output_dir else default_output_dir(ticker, date, args.suffix)
    names, isin = load_ticker_meta(ticker)

    # --- News supplement ------------------------------------------------------
    if args.supplement:
        news_out = args.supplement  # validated by type=supplement
        print(f">> News: verwende vorhandenes Supplement: {news_out}")
    elif args.dry_run:
        news_out = None
        print(">> dry-run: News-Pipeline übersprungen")
    elif args.no_news:
        candidate = out / f"news_supplement_{ticker}_{date}.json"
        if not (candidate.is_file() and candidate.stat().st_size > 0):
            print(f"Warnung: kein Supplement unter {candidate} (--no-news)")
        news_out = str(candidate) if candidate.is_file() else None
    else:
        candidate = out / f"news_supplement_{ticker}_{date}.json"
        ok = build_news(ticker, date, names, isin, out, candidate)
        news_out = str(candidate) if ok else None

    # --- LLM selection (ta.sh logic) ------------------------------------------
    # --api-url → custom profile; --llm=NAME → forced; --quick → local swift
    # model with low effort + 1/1 rounds; otherwise lmstudio xhigh 3/2.
    debate = args.debate_rounds if args.debate_rounds is not None else (1 if args.quick else 3)
    risk = args.risk_rounds if args.risk_rounds is not None else (1 if args.quick else 2)

    spec = dict(
        ticker=ticker, date=date, analysts=args.analysts, output_dir=out,
        timeout=args.timeout, news_supplement=news_out, portfolio=args.portfolio,
        dry_run=args.dry_run, ui=args.ui, debate_rounds=debate, risk_rounds=risk,
        llm="lmstudio", lmstudio_url=None, weber_url=None,
        lmstudio_model=None, weber_model=None,
        custom_api_url=None, custom_model=None,
        lmstudio_reasoning_effort=None, custom_reasoning_effort=None,
        custom_api_key_env=args.custom_api_key_env,
    )
    if args.api_url:
        spec.update(llm="custom", custom_api_url=args.api_url, custom_model=args.model)
        if args.reasoning_effort:
            spec["custom_reasoning_effort"] = args.reasoning_effort
    elif args.llm:
        spec["llm"] = args.llm
        if args.reasoning_effort and args.llm == "lmstudio":
            spec["lmstudio_reasoning_effort"] = args.reasoning_effort
    elif args.quick:
        # quick = local swift model, low effort; an explicit --reasoning-effort
        # or --lmstudio-model beats the profile defaults (A/B runs).
        spec.update(lmstudio_model=QUICK_LLM_MODEL,
                    lmstudio_reasoning_effort=args.reasoning_effort or QUICK_REASONING_EFFORT)
    else:
        spec["lmstudio_reasoning_effort"] = args.reasoning_effort or "xhigh"
    if args.lmstudio_model:
        spec["lmstudio_model"] = args.lmstudio_model
    if args.weber_model:
        spec["weber_model"] = args.weber_model
    if args.lmstudio_url:
        spec["lmstudio_url"] = args.lmstudio_url
    if args.weber_url:
        spec["weber_url"] = args.weber_url

    # --- Background start (ta.sh --bg): news already built in the foreground,
    # the detached child reuses the supplement and never rebuilds it. ---------
    if args.bg:
        child = [sys.executable, str(Path(__file__).resolve()), "run", ticker, date]
        if args.quick:
            child.append("--quick")
        if getattr(args, "allow_copilot", False):
            child.append("--allow-copilot")
        for flag, val in (("--llm", args.llm), ("--api-url", args.api_url), ("--model", args.model),
                          ("--reasoning-effort", args.reasoning_effort),
                          ("--lmstudio-model", args.lmstudio_model), ("--weber-model", args.weber_model),
                          ("--analysts", None if args.analysts == "market,news,fundamentals,social" else args.analysts),
                          ("--debate-rounds", str(debate) if args.debate_rounds is not None else None),
                          ("--risk-rounds", str(risk) if args.risk_rounds is not None else None),
                          ("--timeout", str(args.timeout)),
                          ("--portfolio", args.portfolio), ("--suffix", args.suffix),
                          ("--output-dir", args.output_dir),
                          ("--custom-api-key-env", args.custom_api_key_env),
                          ("--news-window-days", str(args.news_window_days) if args.news_window_days else None),
                          ("--event-window-days", str(args.event_window_days) if args.event_window_days else None)):
            if val:
                child.append(f"{flag}={val}")
        if getattr(args, "no_event_context", False):
            child.append("--no-event-context")
        if news_out:
            child.append(f"--supplement={news_out}")
        else:
            child.append("--no-news")
        log = out / "ta_run.log"
        with open(log, "a", encoding="utf-8") as f:
            proc = subprocess.Popen(child, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        print(json.dumps({"ok": True, "bg": True, "pid": proc.pid, "log": str(log),
                          "output_dir": str(out)}, ensure_ascii=False))
        return

    execute_run(argparse.Namespace(**spec))


def execute_run(a: argparse.Namespace) -> None:
    """The run machinery (unchanged from the pre-merge wrapper): profile chain,
    secure child env, dry-run JSON, TUI window, fallback loop, provenance."""
    if not 60 <= a.timeout <= 14400:
        fail("timeout must be between 60 and 14400 seconds")
    out = safe_dir(str(a.output_dir))
    if a.news_supplement:
        try:
            validate_supplement(a.news_supplement, a.ticker, a.date)
        except ValueError as exc:
            fail(f"news supplement rejected: {exc}")
    if a.lmstudio_url:
        os.environ["LMSTUDIO_BASE_URL"] = a.lmstudio_url
    if a.lmstudio_model:
        os.environ["LMSTUDIO_MODEL"] = a.lmstudio_model
    if a.lmstudio_reasoning_effort:
        os.environ["LMSTUDIO_REASONING_EFFORT"] = a.lmstudio_reasoning_effort
    if a.weber_url:
        os.environ["WEBERCLOUD_BASE_URL"] = a.weber_url
    if a.weber_model:
        os.environ["WEBERCLOUD_MODEL"] = a.weber_model
    if a.custom_api_url:
        os.environ["CUSTOM_LLM_BASE_URL"] = a.custom_api_url
    if a.custom_model:
        os.environ["CUSTOM_LLM_MODEL"] = a.custom_model
    if a.custom_reasoning_effort:
        os.environ["CUSTOM_LLM_REASONING_EFFORT"] = a.custom_reasoning_effort
    if a.custom_api_key_env:
        os.environ["CUSTOM_LLM_API_KEY_ENV"] = a.custom_api_key_env

    if a.llm == "auto":
        chain = [c for c in LLM_CHAIN if _profile_precheck(c)[0]]
        if not chain:
            fail("No LLM backend is available: copilot needs COPILOT_GITHUB_TOKEN, "
                 "lmstudio a reachable LM Studio server, webercloud "
                 "HERMES_CUSTOM_WEBER_CLOUD_API_KEY + a reachable endpoint. "
                 "Or force one via --llm.")
    else:
        ok, reason = _profile_precheck(a.llm)
        if not ok:
            fail(f"LLM profile '{a.llm}' is not available: {reason}.")
        chain = [a.llm]

    if "copilot" in chain:
        # Never silent: the ToS note goes to the human on stderr and into
        # provenance; stdout stays a clean JSON contract.
        print(f"WARNUNG: {COPILOT_TOS_NOTE}", file=sys.stderr)

    # Hand-curated sources (German portal feeds, forum threads, Reddit search
    # terms) live in references/hardcoded_sources.yaml next to this script. The
    # TRADINGAGENTS_ prefix makes build_child_env forward the path into the graph
    # process, where vendors/reddit.py reads it lazily.
    if not os.environ.get("TRADINGAGENTS_REDDIT_OVERRIDES"):
        sources_yaml = Path(__file__).resolve().parent.parent / "references" / "hardcoded_sources.yaml"
        if sources_yaml.is_file():
            os.environ["TRADINGAGENTS_REDDIT_OVERRIDES"] = str(sources_yaml)

    root = find_install_root()
    prompts = prompt_versions(root)
    cmd = discover_command(root) + ["--ticker", a.ticker, "--date", a.date, "--analysts", a.analysts, "--save", "--no-show", "--html"]
    if a.portfolio:
        cmd += ["--portfolio", a.portfolio]
    result = {"ok": True, "read_only": True, "ticker": a.ticker, "date": a.date, "analysts": a.analysts.split(','), "command": cmd, "output_dir": str(out),
              "llm_chain": chain, "debate_rounds": a.debate_rounds, "risk_rounds": a.risk_rounds,
              "news_supplement": a.news_supplement, "portfolio": a.portfolio,
              "reddit_overrides": os.environ.get("TRADINGAGENTS_REDDIT_OVERRIDES")}
    if a.dry_run:
        first = _resolve_profile(chain[0])
        result.update({"dry_run": True, "llm": chain[0],
                       "llm_backend_url": first.get("TRADINGAGENTS_LLM_BACKEND_URL"),
                       "llm_model": first.get("TRADINGAGENTS_QUICK_THINK_LLM")})
        print(json.dumps(result, indent=2, ensure_ascii=False)); return

    if a.ui:
        # Same pipeline, same env — but on a real TTY in its own window so the
        # upstream rich Live layout (message log, agent panels, report panels,
        # timings) renders instead of being captured into a log. The backend
        # prechecks above already guarantee `chain[0]` is reachable.
        if not shutil.which("gnome-terminal"):
            fail("--ui needs gnome-terminal (not found on this system); "
                 "run without --ui for the headless variant.")
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            fail("--ui needs a graphical session (DISPLAY/WAYLAND_DISPLAY missing).")
        prof = chain[0]
        llm_defaults = _resolve_profile(prof)
        env = build_child_env(out, prof, llm_defaults,
                              a.debate_rounds, a.risk_rounds, a.news_supplement)
        effort = effective_effort(env)
        cmd = discover_command(root) + ["--ticker", a.ticker, "--date", a.date,
                                        "--analysts", a.analysts,
                                        "--save", "--show", "--html"]
        if a.portfolio:
            cmd += ["--portfolio", a.portfolio]
        title = f"TradingAgents {a.ticker} {a.date}"
        inner = " ".join(shlex.quote(part) for part in cmd)
        # The window's stdout is a TTY (that is the point of --ui), so nothing
        # can be captured here. The child records its own exit code in a file;
        # that plus the saved report tree is the run's audit trail.
        exit_file = out / ".run_exit_code"
        script = "\n".join([
            f"cd {shlex.quote(str(root))}",
            inner,
            "rc=$?",
            f"printf '%s' \"$rc\" > {shlex.quote(str(exit_file))}",
            "echo",
            "echo '--- TradingAgents-Run beendet (Exit-Code $rc) ---'",
            "echo 'Das Fenster bleibt offen; Enter schliesst es.'",
            "read -r _ || true",
            "exit $rc",
        ])
        print(json.dumps({"ui": True, "window": "gnome-terminal", "title": title,
                          "llm": prof, "output_dir": str(out),
                          "note": "Starting the TUI window; closing it ends this call."},
                         ensure_ascii=False))
        # The terminal client needs the desktop session's variables to reach the
        # terminal server; the windowed shell inherits this whole environment,
        # so it also receives the TRADINGAGENTS_* prefill (models, rounds, ...).
        gt_env = dict(env)
        for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_DATA_HOME",
                  "XDG_CONFIG_HOME", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
                  "DBUS_SESSION_BUS_ADDRESS", "GDK_BACKEND"):
            v = os.environ.get(k)
            if v:
                gt_env[k] = v
        started = time.time()
        try:
            exit_file.unlink()
        except OSError:
            pass
        subprocess.run(["gnome-terminal", "--title", title, "--geometry", "170x48",
                        "--wait", "--", "bash", "-c", script], env=gt_env)
        rc = None
        try:
            rc = int(exit_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
        try:
            exit_file.unlink()
        except OSError:
            pass
        files = report_files(out)
        matrix = read_coverage_matrix(out)
        # An audit trail is the point: without this, a --ui run (the default
        # interactive path) left no machine-readable record of what ran.
        provenance = {
            "wrapper_version": WRAPPER_VERSION, "ui": True,
            "timed_out": False, "partial": rc not in (0, None),
            "returncode": rc,
            "started_utc": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(),
            "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "ticker": a.ticker, "analysis_date": a.date,
            "analysts": a.analysts.split(','),
            "debate_rounds": a.debate_rounds, "risk_rounds": a.risk_rounds,
            "news_supplement": a.news_supplement,
            "llm_profile": prof,
            "llm_model": llm_defaults.get("TRADINGAGENTS_QUICK_THINK_LLM"),
            "reasoning_effort": effort,
            "llm_backend_url": llm_defaults.get("TRADINGAGENTS_LLM_BACKEND_URL"),
            "llm_chain": [prof],
            "command": cmd,
            "report_files": files,
            "data_coverage": matrix,
            "data_coverage_source": "report" if matrix else None,
            "quality_gate": read_quality_gate(out),
            "advisory": read_advisory(out),
            "prompt_versions": prompts,
            "analysis_windows": analysis_windows_summary(run_date(a)),
            "replay_id": replay_id(a, prof, prompts, effort),
            "copilot_enabled": _copilot_allowed(),
            "copilot_tos_note": COPILOT_TOS_NOTE if prof == "copilot" else None,
            "console_output": "tty (--ui); no tradingagents.log — see the saved report tree",
        }
        prov = write_provenance(out, provenance)
        result = {"ok": bool(files), "ui": True, "ticker": a.ticker, "date": a.date,
                  "returncode": rc,
                  "analysts": a.analysts.split(","), "llm": prof,
                  "output_dir": str(out), "report_files": files,
                  "provenance": str(prov), "data_coverage": matrix,
                  "quality_gate": read_quality_gate(out),
                  "advisory": read_advisory(out),
                  "note": "TUI ran in its own terminal window; console output was on "
                          "the TTY (no log file), the saved report files are the result."}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    attempts = []
    for idx, prof in enumerate(chain):
        llm_defaults = _resolve_profile(prof)
        env = build_child_env(out, prof, llm_defaults,
                              a.debate_rounds, a.risk_rounds, a.news_supplement)
        effort = effective_effort(env)
        started = time.time()
        timed_out = False
        try:
            cp = subprocess.run(cmd, cwd=root, env=env, text=True, capture_output=True, timeout=a.timeout, shell=False)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            raw = exc.output
            if raw is None:
                raw = ["", ""]
            elif isinstance(raw, (list, tuple)):
                raw = [("" if o is None else o) for o in raw]
            else:
                raw = [raw, ""]
            raw = [_to_text(o) for o in raw]
            cp = subprocess.CompletedProcess(cmd, 124, raw[0], raw[1])
        elapsed = round(time.time() - started, 2)
        log_text = "STDOUT\n" + _to_text(cp.stdout) + "\nSTDERR\n" + _to_text(cp.stderr)
        log = out / "tradingagents.log"
        log.write_text(log_text, encoding="utf-8")
        try: log.chmod(0o600)
        except OSError: pass
        # Per-attempt copy so earlier attempts stay diagnosable after the chain moves on.
        attempt_log = out / f"tradingagents.{prof}.log"
        attempt_log.write_text(log_text, encoding="utf-8")
        try: attempt_log.chmod(0o600)
        except OSError: pass
        files = report_files(out)
        data_coverage = build_data_coverage(log_text)
        # The report's own matrix (routing-layer records) beats the log view:
        # it also exists in --ui mode and names the vendor that served a call.
        coverage_matrix = read_coverage_matrix(out)
        data_coverage["matrix"] = coverage_matrix
        fallback_reason = _fallback_reason(cp.returncode, timed_out, files, log_text)
        attempts.append({
            "attempt": idx + 1, "llm_profile": prof,
            "llm_model": llm_defaults.get("TRADINGAGENTS_QUICK_THINK_LLM"),
            "llm_backend_url": llm_defaults.get("TRADINGAGENTS_LLM_BACKEND_URL"),
            "returncode": cp.returncode, "timed_out": timed_out,
            "elapsed_seconds": elapsed, "report_files": len(files),
            "fallback_reason": fallback_reason, "log": str(attempt_log),
        })
        if cp.returncode == 0:
            prov = out / "provenance.json"
            provenance={"wrapper_version":WRAPPER_VERSION,"timed_out":False,"partial":False,
                        "started_utc":dt.datetime.fromtimestamp(started,dt.timezone.utc).isoformat(),"finished_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
                        "ticker":a.ticker,"analysis_date":a.date,"analysts":a.analysts.split(','),
                        "debate_rounds":a.debate_rounds,"risk_rounds":a.risk_rounds,"news_supplement":a.news_supplement,
                        "llm_profile":prof,"llm_model":llm_defaults.get("TRADINGAGENTS_QUICK_THINK_LLM"),
                        "llm_backend_url":llm_defaults.get("TRADINGAGENTS_LLM_BACKEND_URL"),
                        "llm_chain":chain,"attempts":attempts,
                        "command":cmd,"report_files":files,"data_coverage":data_coverage,"prompt_versions": prompts,
                        "quality_gate": read_quality_gate(out),
                        "advisory": read_advisory(out),
                        "analysis_windows": analysis_windows_summary(run_date(a)),
                        "replay_id":replay_id(a,prof,prompts,effort),
                        "reasoning_effort":effort,
                        "copilot_enabled":_copilot_allowed(),
                        "copilot_tos_note":COPILOT_TOS_NOTE if prof == "copilot" else None}
            prov.write_text(json.dumps(provenance,indent=2),encoding="utf-8")
            result.update({"llm": prof, "elapsed_seconds": elapsed, "log": str(log), "provenance": str(prov),
                           "attempts": attempts, "report_files": files, "data_coverage": data_coverage,
                           "quality_gate": read_quality_gate(out),
                           "advisory": read_advisory(out)})
            print(json.dumps(result, indent=2, ensure_ascii=False)); return
        # Failed. If it looks like an LLM-backend fault and backends remain, fall back.
        if fallback_reason and idx < len(chain) - 1:
            continue
        prov = out / "provenance.json"
        provenance={"wrapper_version":WRAPPER_VERSION,"timed_out":timed_out,"partial":timed_out or cp.returncode != 0,
                    "started_utc":dt.datetime.fromtimestamp(started,dt.timezone.utc).isoformat(),"finished_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
                    "ticker":a.ticker,"analysis_date":a.date,"analysts":a.analysts.split(','),
                    "debate_rounds":a.debate_rounds,"risk_rounds":a.risk_rounds,"news_supplement":a.news_supplement,
                    "llm_profile":prof,"llm_model":llm_defaults.get("TRADINGAGENTS_QUICK_THINK_LLM"),
                    "llm_backend_url":llm_defaults.get("TRADINGAGENTS_LLM_BACKEND_URL"),
                    "llm_chain":chain,"attempts":attempts,
                    "command":cmd,"report_files":files,"data_coverage":data_coverage,"prompt_versions": prompts,
                        "analysis_windows": analysis_windows_summary(run_date(a)),
                        "replay_id":replay_id(a,prof,prompts,effort),
                        "reasoning_effort":effort,
                        "copilot_enabled":_copilot_allowed(),
                        "copilot_tos_note":COPILOT_TOS_NOTE if prof == "copilot" else None}
        prov.write_text(json.dumps(provenance,indent=2),encoding="utf-8")
        if timed_out:
            fail(f"TradingAgents timed out after {a.timeout}s (profile {prof}); "
                 f"partial log: {log}, provenance: {prov}", 124)
        extra = f" [fallback: {fallback_reason}]" if fallback_reason else ""
        fail(f"TradingAgents failed with exit code {cp.returncode} (profile {prof}){extra}; "
             f"see {log}, provenance: {prov}", cp.returncode)
    raise AssertionError("unreachable")


_EPILOG = """\
examples:
  run_analysis.py run RHM.DE            # voller lokaler Run (lmstudio xhigh, 3/2 Runden, ~3-4 h)
  run_analysis.py run RHM.DE --quick    # lokales swift-Modell, low effort, 1/1 Runde (~30-90 min)
  run_analysis.py run RHM.DE --bg       # im Hintergrund (PID + Log-Pfad)
  run_analysis.py run RHM.DE --ui       # volle TUI in eigenem gnome-terminal-Fenster
  run_analysis.py run RHM.DE --portfolio=~/.tradingagents/portfolio.example.json
  run_analysis.py news RHM.DE           # nur das News-Supplement bauen (Pfad nachdrucken)
  run_analysis.py doctor RHM.DE         # Pre-flight: Exit 0 ok / 3 degraded / 4 blocked
  run_analysis.py run RHM.DE 2026-11-03 # anderes Analyse-Datum
  run_analysis.py run RHM.DE --dry-run  # nur das Child-Command anzeigen (kein News-Build, kein LLM-Call)
"""


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface (separate from main() so tests can parse arguments)."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=_EPILOG)
    sub = ap.add_subparsers(dest="cmd")

    pr = sub.add_parser("run", help="News-Pipeline + voller Run",
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pr.add_argument("ticker", type=ticker, help="Ticker, z. B. RHM.DE")
    pr.add_argument("date", nargs="?", type=iso_date, default=None, help="Analyse-Datum YYYY-MM-DD (Default: heute)")
    pr.add_argument("--quick", action="store_true",
                    help=f"lokales swift-Modell ({QUICK_LLM_MODEL}), {QUICK_REASONING_EFFORT} effort, 1/1 Runde")
    pr.add_argument("--news-window-days", type=positive_days, default=None,
                    help=f"Schlagzeilen-/Sentiment-Fenster in Tagen (Default {7}; Env {NEWS_WINDOW_ENV})")
    pr.add_argument("--event-window-days", type=positive_days, default=None,
                    help=f"Event-Kontext-Fenster in Tagen, First-Party (Default 180; Env {EVENT_WINDOW_ENV})")
    pr.add_argument("--no-event-context", action="store_true",
                    help="Event-Kontext (180-Tage-Block) nicht in den News-Prompt injizieren")
    pr.add_argument("--no-news", action="store_true", help="News-Pipeline überspringen; vorhandenes Supplement nutzen, wenn vorhanden")
    pr.add_argument("--bg", action="store_true", help="im Hintergrund starten (detached), Log + PID ausgeben")
    pr.add_argument("--ui", action="store_true",
                    help="Run in der vollen Framework-TUI in einem eigenen gnome-terminal-Fenster (nicht mit --bg kombinierbar)")
    pr.add_argument("--dry-run", action="store_true", help="nur das Child-Command anzeigen (kein News-Build, kein LLM-Call)")
    pr.add_argument("--llm", choices=["auto", "copilot", "lmstudio", "webercloud"], default=None,
                    help="Backend erzwingen; Default: lmstudio (--quick: swift low)")
    pr.add_argument("--allow-copilot", action="store_true",
                    help="Copilot-Backend für diesen Lauf freischalten (Fork-only, ToS-Grauzone; Default: aus)")
    pr.add_argument("--api-url", default=None,
                    help="generisches OpenAI-kompatibles Backend (impliziert custom); muss mit --model kombiniert werden")
    pr.add_argument("--model", default=None, help="Modell-Name für --api-url (z. B. unsloth/qwen3.8-27b)")
    pr.add_argument("--portfolio", type=portfolio_file, default=None,
                    help="JSON mit AKTUELLEN Holdings+Cash (PortfolioContext); "
                         "'equity' nötig für die deterministische Positionsgröße")
    pr.add_argument("--analysts", type=analysts, default="market,news,fundamentals,social")
    pr.add_argument("--debate-rounds", type=rounds, default=None, help="Default 3 (--quick: 1); explizit gesetzt schlägt --quick")
    pr.add_argument("--risk-rounds", type=rounds, default=None, help="Default 2 (--quick: 1); explizit gesetzt schlägt --quick")
    pr.add_argument("--timeout", type=int, default=14400, help="Sekunden (60..14400)")
    pr.add_argument("--lmstudio-model", default=None, help="Modell-Pin (A/B-Runs; überschreibt auch das quick-Modell)")
    pr.add_argument("--weber-model", default=None, help="Modell-Pin (A/B-Runs)")
    pr.add_argument("--reasoning-effort", choices=["minimal", "low", "medium", "high", "xhigh"], default=None,
                    help="Reasoning-Effort für lmstudio (Default xhigh, quick: low) und custom (nur wenn gesetzt)")
    pr.add_argument("--suffix", type=suffix_value, default=None, help="Output-Dir-Suffix <DATE>-<S> (A/B-Runs, Retries)")
    pr.add_argument("--supplement", type=supplement, default=None, help="bestehendes Supplement statt Pipeline")
    pr.add_argument("--output-dir", default=None, help="Output-Verzeichnis überschreiben (Default: ~/.tradingagents/reports/trading-scenarios/<TICKER>/<DATE>[-<SUFFIX>])")
    pr.add_argument("--lmstudio-url", default=None, help="LM Studio base URL (Default $LMSTUDIO_BASE_URL, dann Profil-.env, dann http://localhost:1234/v1)")
    pr.add_argument("--weber-url", default=None, help="Weber-Cloud base URL (Default $WEBERCLOUD_BASE_URL)")
    pr.add_argument("--custom-api-key-env", default=None,
                    help="Name einer Env-Var im Profil-.env mit dem API-Key für ein Remote-Custom-Endpoint")
    pr.set_defaults(func=cmd_run)

    pn = sub.add_parser("news", help="nur das News-Supplement bauen",
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pn.add_argument("ticker", type=ticker, help="Ticker, z. B. RHM.DE")
    pn.add_argument("date", nargs="?", type=iso_date, default=None, help="Analyse-Datum YYYY-MM-DD (Default: heute)")
    pn.add_argument("--facts", action="store_true",
                    help="zusätzlich einen IR-Fakten-Snapshot bauen (news_collect.py facts)")
    pn.set_defaults(func=cmd_news)

    pdoc = sub.add_parser("doctor", help="Pre-flight für unbeaufsichtigte Runs (Exit 0 ok / 3 degraded / 4 blocked)",
                          formatter_class=argparse.RawDescriptionHelpFormatter)
    pdoc.add_argument("ticker", nargs="?", type=ticker, default=None, help="optional: Ticker für ticker-spezifische Checks")
    pdoc.add_argument("date", nargs="?", type=iso_date, default=None, help="optional: Analyse-Datum YYYY-MM-DD")
    pdoc.add_argument("--llm", choices=["auto", "copilot", "lmstudio", "webercloud"], default=None,
                      help="nur dieses Backend prüfen (Default: auto = alle in der Kette)")
    pdoc.add_argument("--allow-copilot", action="store_true",
                      help="Copilot als verfügbar prüfen (sonst meldet der Check 'disabled by feature flag')")
    pdoc.add_argument("--portfolio", default=None, help="Portfolio-JSON mitprüfen")
    pdoc.add_argument("--supplement", default=None, help="News-Supplement mitprüfen")
    pdoc.add_argument("--news-window-days", type=positive_days, default=None,
                      help="Schlagzeilen-Fenster, das der Lauf nutzen würde")
    pdoc.add_argument("--event-window-days", type=positive_days, default=None,
                      help="Event-Kontext-Fenster, das der Lauf nutzen würde")
    pdoc.add_argument("--no-event-context", action="store_true",
                      help="Event-Kontext als aus prüfen")
    pdoc.set_defaults(func=cmd_doctor)

    return ap


def main() -> None:
    ensure_venv_python()

    for tok in sys.argv[1:]:
        if tok in VALUE_FLAGS:
            fail(f"Flag {tok} braucht die Equals-Form ({tok}=wert) — Space-Form ist ein Hard Error")

    ap = build_parser()
    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        raise SystemExit(1)
    args.func(args)


if __name__ == "__main__": main()
