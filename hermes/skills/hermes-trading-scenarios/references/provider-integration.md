# TradingAgents LLM provider integration

Use this only when configuring, changing, or upgrading an LLM route; the normal scenario workflow can rely on the established wrapper defaults.

## Preflight sequence

1. Inspect the installed TradingAgents version, provider factory, config environment overrides, endpoint URL, and the actual package import path. A project source edit has no effect if Python still imports a copied `site-packages` install; use `pip install --no-deps --editable .` when the checkout itself must supply the runtime code, then verify `tradingagents.__file__` points into that checkout.
2. Confirm provider/model/reasoning settings are actually consumed by both quick and deep tiers. A CLI banner alone does not prove which API route the LangChain client sends.
3. Through the same client factory and credentials used by the full run, make a small plain-text request and a structured-output request. Assert the parsed schema result. Only then run an end-to-end ticker analysis; this catches endpoint-family or tool/structured-output incompatibilities before the graph spends many model calls.
4. Run the wrapper's `--dry-run`, then one bounded end-to-end analysis. Verify the exact returned report paths and provenance file; do not infer output locations from a prior release. Report missing optional data sources as gaps rather than treating them as successful coverage.

## GitHub Copilot GPT-5+/GPT-6 route

- Use `https://api.githubcopilot.com` as the base URL and the Copilot **Responses API**. GPT-6 requests to `/chat/completions` can return `unsupported_api_for_model` even when the credential is valid.
- In `ChatOpenAI`, enable `use_responses_api=True`, pass the selected `reasoning_effort`, and provide Copilot attribution headers: `Editor-Version`, `User-Agent`, `Copilot-Integration-Id`, `Openai-Intent`, and `x-initiator`.
- Keep this behavior conditional on a Copilot hostname so normal OpenAI and unrelated compatible endpoints retain their existing wire formats.
- Read the token only from the active `$HERMES_HOME/.env` at run time, pass only the model credential needed by the child process, and never print it or persist it in the TradingAgents checkout, reports, or provenance. Filter unrelated Hermes credentials and remove inherited `PYTHONPATH` before launching the venv CLI.

## OpenRouter route (cloud fallback via the custom profile)

Use when the local LM Studio host is unreachable and a run is needed immediately. OpenRouter is OpenAI-compatible, so no fork change is required — the wrapper's `custom` profile carries it.

1. **Resolve the model id first; colloquial names are not ids.** `curl -s https://openrouter.ai/api/v1/models` and filter `id`/`name` for the family (`openai/gpt-6-luna`, `openai/gpt-6-luna-pro`, `openai/gpt-5.6-luna`, `openai/gpt-5.6-luna-pro`). A name like "luna max" matches nothing — ask which variant is meant, or read "max" as the newest non-Pro Luna at `reasoning_effort=xhigh`.
2. **Confirm the endpoint honours the effort parameter:** `GET /api/v1/models/<author>/<slug>/endpoints` and check `supported_parameters` contains `reasoning_effort`.
3. **Probe once, cheaply:** a 32-token completion carrying the intended `reasoning_effort`; assert HTTP 200 and `usage.completion_tokens_details.reasoning_tokens > 0`. A 400 here is a wrong effort string, not a bad credential. Print `usage.cost` — it is the cheapest cost check available before a multi-hour run.
4. **Run:** `run_analysis.py run TICKER --api-url=https://openrouter.ai/api/v1 --model=openai/gpt-6-luna --custom-api-key-env=OPENROUTER_API_KEY --reasoning-effort=xhigh --debate-rounds=1 --bg`. The key comes from the profile `.env` through `--custom-api-key-env`; on manual curl probes send `HTTP-Referer`/`X-Title` so the traffic is attributable.
5. **Expect single-attempt semantics.** The custom profile applies its 1800 s per-request timeout and does **not** join the `auto` chain, so a mid-run OpenRouter failure ends the run — budget `--timeout` generously and resume from the checkpoint rather than expecting a silent backend switch.

Pitfall: the wrapper's reachability precheck counts any HTTP response as up, so an OpenRouter base URL looks "available" even with a dead key or a misspelled model. Only the step-3 probe proves key, model id, and effort together.
