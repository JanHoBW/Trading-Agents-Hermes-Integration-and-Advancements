"""Shared issuer-registry lookup (German equities).

One file, one lookup: the registry under ``~/.tradingagents/german_equities.json``
(override ``TRADINGAGENTS_ISSUER_REGISTRY``) maps a ticker to its issuer
identity, and vendors add their own sub-block (``ureg_query_name`` for the
register, ``news_feed_url`` for RSS, ``ir`` for the issuer-site fetcher). Keeping
the resolution here means a new data source does not re-implement the matching
rules (ticker, ISIN, WKN, Yahoo/Alpha symbols, aliases).
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError


def load_instrument(ticker: str, config: dict | None = None) -> dict:
    """The registry record for ``ticker`` (or any of its aliases).

    ``config`` is an explicit override for tests and callers that already hold a
    resolved config; otherwise the active dataflow config is used.

    Raises ``VendorNotConfiguredError`` when the registry or the ticker is not
    configured — that is what lets a vendor self-select and the routing chain
    fall through to the next one.
    """
    cfg = config if config is not None else get_config()
    path = Path(cfg.get("issuer_registry_path", "~/.tradingagents/german_equities.json")).expanduser()
    if not path.is_file():
        raise VendorNotConfiguredError(f"issuer registry not found: {path}")
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VendorUnavailableError(f"could not read issuer registry {path}: {exc}") from None
    instruments = registry.get("instruments", {}) if isinstance(registry, dict) else {}
    if not isinstance(instruments, dict):
        raise VendorUnavailableError("issuer registry 'instruments' must be a JSON object")
    wanted = ticker.strip().upper()
    for key, record in instruments.items():
        if not isinstance(record, dict):
            continue
        aliases = record.get("aliases", [])
        candidates = [
            key,
            record.get("ticker"),
            record.get("isin"),
            record.get("wkn"),
            record.get("yahoo_symbol"),
            record.get("alpha_vantage_symbol"),
            *(aliases if isinstance(aliases, list) else []),
        ]
        if any(str(candidate).strip().upper() == wanted for candidate in candidates):
            feed_url = record.get("news_feed_url")
            if feed_url and (urlparse(feed_url).scheme != "https" or not urlparse(feed_url).hostname):
                raise VendorNotConfiguredError(f"issuer feed URL for {ticker} must use HTTPS")
            return {**record, "ticker": key, "news_feed_url": feed_url}
    raise VendorNotConfiguredError(f"no issuer identity/feed configured for {ticker}")
