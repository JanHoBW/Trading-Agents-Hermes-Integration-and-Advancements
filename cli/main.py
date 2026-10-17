import sys
from enum import StrEnum
from typing import Annotated

import typer
from rich.markup import escape

from cli.display import console
from cli.models import AnalystType, AssetType
from cli.prompts import filter_analysts_for_asset_type, parse_analysts
from cli.run import run_analysis
from tradingagents.backtest import iter_grid, run_backtest, summarize
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.memory import TradingMemoryLog
from tradingagents.llm_clients.openai_client import SIGNED_IN_PROVIDERS
from tradingagents.portfolio import load_portfolio

# prompt_toolkit's win32 output module is importable only on Windows (it asserts
# the platform at import time), so gate on the platform rather than catching the
# failure — that way a genuinely broken prompt_toolkit on Windows still surfaces
# instead of silently disabling the handler below. Off Windows this stays an
# empty tuple, which `except` accepts and never matches (#1138).
if sys.platform == "win32":  # pragma: no cover - platform dependent
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError

    _NO_CONSOLE_ERRORS: tuple[type[BaseException], ...] = (NoConsoleScreenBufferError,)
else:
    _NO_CONSOLE_ERRORS = ()

app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # Enable shell completion
)


@app.callback(invoke_without_command=True)
def analyze(
    ctx: typer.Context,
    checkpoint: bool | None = typer.Option(
        None,
        "--checkpoint/--no-checkpoint",
        help="Enable/disable checkpoint-resume (save state after each node so a "
        "crashed run can resume). Omit to honor TRADINGAGENTS_CHECKPOINT_ENABLED.",
    ),
    clear_checkpoints: bool = typer.Option(
        False,
        "--clear-checkpoints",
        help="Delete all saved checkpoints before running (force fresh start).",
    ),
    portfolio: str = typer.Option(
        None,
        "--portfolio",
        help="JSON file with current holdings and cash, so the trader, risk and "
        "portfolio agents size against your actual position.",
    ),
    ticker: str = typer.Option(None, "--ticker", help="Ticker to analyze, e.g. NVDA or 0700.HK; skips the prompt"),
    date: str = typer.Option(None, "--date", help="Analysis date, YYYY-MM-DD; skips the prompt"),
    analysts: str = typer.Option(
        None, "--analysts", help="Comma-separated analysts, e.g. market,news; skips the prompt"
    ),
    save: bool | None = typer.Option(
        None, "--save/--no-save", help="Save the report under results_dir without asking"
    ),
    show: bool | None = typer.Option(
        None, "--show/--no-show", help="Show the full report at the end without asking"
    ),
    html: bool | None = typer.Option(
        None, "--html/--no-html",
        help="Save the report as one HTML page too, complete_report.html, without asking (default: yes)",
    ),
):
    """Run an analysis. This is what a bare `tradingagents` does.

    Flags answer their questions; with provider, models, depth and language also
    set through TRADINGAGENTS_* variables, the run asks nothing.
    """
    if ctx.invoked_subcommand is not None:
        return
    if clear_checkpoints:
        from tradingagents.graph.checkpointer import clear_all_checkpoints
        n = clear_all_checkpoints(DEFAULT_CONFIG["data_cache_dir"])
        console.print(f"[yellow]Cleared {n} checkpoint(s).[/yellow]")
    portfolio_context = None
    if portfolio:
        try:
            portfolio_context = load_portfolio(portfolio)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from None

    try:
        flags = {"ticker": ticker, "date": date, "analysts": analysts, "save": save, "show": show, "html": html}
        run_analysis(checkpoint=checkpoint, portfolio=portfolio_context, flags=flags)
    except _NO_CONSOLE_ERRORS:
        # A terminal with no console buffer cannot host the interactive prompts.
        # Emit one actionable line on stderr instead of a prompt_toolkit
        # traceback; plain text, since rich may not render here either (#1138).
        typer.echo(
            "Error: no Windows console available. The interactive CLI needs a real "
            "console buffer — run it from Windows Terminal, PowerShell, or cmd.exe "
            "rather than a piped or embedded terminal.",
            err=True,
        )
        raise typer.Exit(code=1) from None


@app.command()
def backtest(
    tickers: str = typer.Argument(..., help="Comma-separated tickers, e.g. NVDA,AAPL"),
    start: str = typer.Option(..., "--start", help="First analysis date, YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="Last analysis date, YYYY-MM-DD"),
    every: int = typer.Option(7, "--every", help="Days between analysis dates"),
    analysts: str = typer.Option(
        None, "--analysts", help="Comma-separated analysts to run: market, sentiment, news, fundamentals; omit for all the asset type allows"
    ),
    asset_type: str = typer.Option("stock", "--asset-type", help="stock or crypto"),
    portfolio: str = typer.Option(
        None, "--portfolio", help="JSON file with holdings and cash, held constant across the grid"
    ),
    run_id: str = typer.Option(
        None, "--run-id", help="Continue an earlier sweep: its cells are skipped and its log reused"
    ),
):
    """Score past decisions over a grid of tickers and dates."""

    try:
        dates = iter_grid(start, end, every)
        book = load_portfolio(portfolio) if portfolio else None
        kind = AssetType(asset_type.strip().lower())
        # The analysts are named and checked as for an analysis; without a
        # choice, every analyst the asset type allows runs.
        chosen = (parse_analysts(analysts, kind) if analysts
                  else filter_analysts_for_asset_type(list(AnalystType), kind))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None

    names = [t.strip() for t in tickers.split(",") if t.strip()]
    if not names:
        console.print("[red]No ticker to analyze; pass them comma-separated, e.g. NVDA,AAPL[/red]")
        raise typer.Exit(code=1)

    def show_progress(done, total, ticker, date):
        console.print(f"[dim][{done}/{total}] {ticker} {date}[/dim]")

    kwargs = {"asset_type": kind.value, "portfolio": book, "run_id": run_id, "progress": show_progress,
              "selected_analysts": [a.value for a in chosen]}

    try:
        result = run_backtest(names, dates, DEFAULT_CONFIG, **kwargs)
    except Exception as exc:  # a missing key or an unknown analyst is a setup error
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    console.print(summarize(result).render())
    console.print(f"\nRan {result.cells_run} cells, skipped {result.skipped}. Log: {result.log_path}")
    console.print(f"Continue or settle this sweep: --run-id {result.run_id}")
    for ticker, date, reason in result.failures:
        console.print(f"[yellow]failed:[/yellow] {ticker} {date}: {reason}")
    for ticker, reason in result.settlement_failures:
        console.print(f"[yellow]unsettled:[/yellow] {ticker}: {reason}")


memory_app = typer.Typer(help="Annotate and read the decision memory log.")
app.add_typer(memory_app, name="memory")

_FEEDBACK_RATINGS = ("gut", "teilweise", "falsch")


def _open_memory_log() -> TradingMemoryLog:
    log = TradingMemoryLog(DEFAULT_CONFIG)
    if not log._log_path or not log._log_path.exists():
        console.print(f"[red]No memory log at {log._log_path} — run a ticker first.[/red]")
        raise typer.Exit(code=1) from None
    return log


@memory_app.command("feedback")
def memory_feedback(
    ticker: str = typer.Argument(..., help="Decision ticker, e.g. RHM.DE"),
    note: str = typer.Option(..., "--note", "-n", help="What was right or wrong about this decision"),
    date: str = typer.Option(None, "--date", "-d", help="Decision date YYYY-MM-DD; omit to annotate the most recent entry of the ticker"),
    rating: str = typer.Option(None, "--rating", "-r", help="gut | teilweise | falsch"),
):
    """Annotate a decision or outcome by hand (human feedback loop).

    The annotation is stored on the entry itself (HUMAN_FEEDBACK block),
    survives settlement rewrites, and is shown to the portfolio manager
    on the next run of the same ticker. Re-annotating replaces the
    previous annotation.
    """
    if rating and rating not in _FEEDBACK_RATINGS:
        console.print(f"[red]--rating must be one of: {', '.join(_FEEDBACK_RATINGS)}[/red]")
        raise typer.Exit(code=1) from None
    log = _open_memory_log()
    entries = [e for e in log.load_entries() if e["ticker"] == ticker]
    if not entries:
        available = ", ".join(sorted({e["ticker"] for e in log.load_entries()})) or "(none)"
        console.print(f"[red]No entries for ticker '{ticker}'. Available: {available}[/red]")
        raise typer.Exit(code=1) from None
    if date is None:
        target = max(e["date"] for e in entries)
    elif any(e["date"] == date for e in entries):
        target = date
    else:
        console.print(f"[red]No entry for {ticker} on {date}[/red]")
        raise typer.Exit(code=1) from None
    if log.add_human_feedback(ticker, target, note, rating=rating):
        console.print(f"[green]Annotated {ticker} {target}[/green] — shown to the portfolio manager next run.")
    else:
        console.print(f"[yellow]Identical annotation already recorded for {ticker} {target} — nothing changed.[/yellow]")


@memory_app.command("show")
def memory_show(
    ticker: str = typer.Argument(None, help="Show only this ticker"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max entries to show (newest first)"),
):
    """Show recent decisions with outcomes, lessons and human feedback."""
    log = _open_memory_log()
    entries = log.load_entries()
    if ticker:
        entries = [e for e in entries if e["ticker"] == ticker]
    for e in list(reversed(entries))[:limit]:
        state = "pending" if e["pending"] else f"settled (raw {e['raw'] or 'n/a'}, alpha {e['alpha'] or 'n/a'})"
        console.print(f"[bold]{e['date']} {e['ticker']} {e['rating']}[/bold] — {state}")
        horizons = e.get("horizons") or {}
        parts = [f"{h}d raw {v['raw']:+.1%}/alpha {v['alpha']:+.1%}"
                 for h, v in sorted(horizons.items())
                 if isinstance(v, dict) and v.get("raw") is not None]
        if parts:
            console.print(f"  horizons: {', '.join(parts)}")
        if e.get("reflection"):
            console.print(f"  lesson: {e['reflection']}")
        hf = e.get("human_feedback")
        if isinstance(hf, dict) and hf.get("vermerk"):
            meta = ", ".join(str(x) for x in (hf.get("datum"), hf.get("bewertung")) if x)
            console.print(f"  [yellow]feedback{(' (' + meta + ')') if meta else ''}:[/yellow] {hf['vermerk']}")


# The plans `login` and `logout` take: the providers signed in to, not keyed.
Plan = StrEnum("Plan", {name: name for name in SIGNED_IN_PROVIDERS})


@app.command()
def login(plan: Annotated[Plan, typer.Argument(help="The plan to sign in with")]):
    """Sign in with your ChatGPT Plus or Pro plan, for the chatgpt provider."""
    from tradingagents.llm_clients import chatgpt_auth

    try:
        # One unwrapped line, so the sign-in address can be copied whole.
        email = chatgpt_auth.sign_in(
            say=lambda line: console.print(line, soft_wrap=True, markup=False, highlight=False))
    except chatgpt_auth.ChatGPTAuthError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from None
    console.print(f"[green]Signed in to ChatGPT as {escape(email)}.[/green]")


@app.command()
def logout(plan: Annotated[Plan, typer.Argument(help="The plan to sign out of")]):
    """Sign out of your ChatGPT plan: revoke this machine's access and forget it."""
    from tradingagents.llm_clients import chatgpt_auth

    outcome = chatgpt_auth.sign_out()
    if outcome == "revoked":
        console.print("Signed out of ChatGPT.")
    elif outcome == "forgotten":
        console.print("[yellow]Signed out here, but OpenAI could not be reached to revoke the sign-in; "
                      "it stays valid at OpenAI until it expires.[/yellow]")
    else:
        console.print("Not signed in to ChatGPT.")


if __name__ == "__main__":
    app()
