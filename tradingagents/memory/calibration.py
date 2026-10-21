"""Calibration layer: aggregate settled multi-horizon outcomes into a compact
record of how well the model's rating direction has actually played out, per
ticker and cross-ticker.

Pure aggregation over the parsed memory log — no LLM call — so it is cheap to
recompute on every run and inject as context. The rating's expected direction is:

    Buy/Overweight  -> positive alpha
    Sell/Underweight -> negative alpha
    Hold            -> roughly flat (|alpha| within a small band)

A "hit" is the alpha at a horizon landing on the expected side of the benchmark.
Aggregates are win-rate per horizon; the anchor horizon (the fast signal) is the
primary one. With no settled, scored entries yet every aggregate is empty and the
rendered line is blank, so this is a no-op until decisions start settling in.

The aggregate is also given a confidence label. A win-rate over three settled
calls is not evidence, and a prompt that presents it as a statistic invites the
Portfolio Manager to anchor on noise — so the label states how much the sample
can carry, pooled across tickers, rather than leaving the reader to guess.
"""

from __future__ import annotations

_BULL = {"buy", "overweight"}
_BEAR = {"sell", "underweight"}
_HOLD = {"hold", "neutral"}
_HOLD_BAND = 0.02  # within ±2% alpha counts as a "hold" hit (no clear move)

# How much a win-rate can carry. Per-instrument samples stay tiny at a realistic
# run cadence, so the level is decided on pooled evidence (cross-ticker anchor
# horizon + realized trades) and a per-instrument figure only ever lowers it.
_CONF_HIGH_N = 20      # settled outcomes needed to call a rate evidence
_CONF_MEDIUM_N = 8
_CONF_FLAT_BAND = (0.35, 0.65)  # a rate inside this band is indistinguishable from chance
_CONF_DISAGREEMENT = 0.25       # ticker vs pooled rate gap that marks instability


def _hit(rating, alpha):
    """True/False whether ``alpha`` landed on ``rating``'s expected side, or
    None when the rating or the alpha is not evaluable."""
    if alpha is None:
        return None
    r = (rating or "").strip().lower()
    if r in _BULL:
        return alpha > 0
    if r in _BEAR:
        return alpha < 0
    if r in _HOLD:
        return abs(alpha) < _HOLD_BAND
    return None


def build_calibration(entries, ticker, anchor, as_of=None):
    """Aggregate outcome hits for ``ticker`` and (cross) the full log.

    ``entries`` are the parsed memory-log dicts (see
    ``TradingMemoryLog.load_entries``); ``anchor`` is the primary horizon.
    Returns::

        {"ticker": {"n": int, "by_horizon": {h: {"n","hits","rate"}},
                    "anchor_rate": rate|None, "realized": {"n","hits","rate"}},
         "cross":  {... same shape over all tickers ...}}

    Only horizons actually present and evaluable count toward a horizon's rate.
    The ``realized`` bucket aggregates recorded real-portfolio P&L (ground
    truth, see ``memory/realized.py``). ``as_of`` (a ``YYYY-MM-DD`` string)
    drops entries that were not yet known by that date, keeping the figure
    point-in-time for backtests (#1251).
    """
    if as_of is not None:
        entries = [e for e in entries
                   if e.get("resolved") and e["resolved"] <= as_of]

    def _agg(subset):
        by_h: dict[int, dict] = {}
        real_n = 0
        real_hits = 0
        for e in subset:
            for h, v in (e.get("horizons") or {}).items():
                hit = _hit(e.get("rating"), (v or {}).get("alpha"))
                if hit is None:
                    continue
                d = by_h.setdefault(h, {"n": 0, "hits": 0})
                d["n"] += 1
                d["hits"] += 1 if hit else 0
            r = e.get("realized")
            if isinstance(r, dict) and r.get("return") is not None:
                hit = _hit(e.get("rating"), r["return"])
                if hit is not None:
                    real_n += 1
                    real_hits += 1 if hit else 0
        for d in by_h.values():
            d["rate"] = (d["hits"] / d["n"]) if d["n"] else None
        realized = {"n": real_n, "hits": real_hits,
                   "rate": (real_hits / real_n) if real_n else None}
        return by_h, realized

    def _pack(subset):
        by_h, realized = _agg(subset)
        n = sum(d["n"] for d in by_h.values()) + realized["n"]
        anchor_rate = by_h[anchor]["rate"] if anchor in by_h else None
        return {"n": n, "by_horizon": by_h, "anchor_rate": anchor_rate, "realized": realized}

    same = [e for e in entries if e.get("ticker") == ticker]
    result = {"ticker": _pack(same), "cross": _pack(entries)}
    # Confidence is a property of the whole record: the pooled sample decides the
    # level, and a per-instrument rate that contradicts it or rests on a handful
    # of calls can only lower it. Never raised by a small local sample.
    result["cross"]["confidence"] = _confidence(result["cross"])
    result["ticker"]["confidence"] = _confidence(result["ticker"], pooled=result["cross"])
    result["confidence"] = result["cross"]["confidence"]
    return result


def _anchor_counts(pack: dict, anchor: int | None = None) -> tuple[int, int | None]:
    """(n, rate) for the pack's anchor horizon, falling back to its largest one."""
    by_h = pack.get("by_horizon") or {}
    key = anchor if anchor in by_h else (min(by_h) if by_h else None)
    if key is None:
        return 0, None
    return by_h[key]["n"], by_h[key].get("rate")


def _confidence(pack: dict, pooled: dict | None = None) -> dict:
    """low | medium | high, with the reason the level is what it is.

    No Brier score: the rating scale is ordinal (Buy … Sell) and carries no
    probability, so there is nothing to score as a forecast. What can be said is
    how many settled outcomes back the rate and whether that rate is
    distinguishable from chance.
    """
    n_own, rate_own = _anchor_counts(pack)
    realized = (pack.get("realized") or {})
    n_real = realized.get("n") or 0
    n_pooled, rate_pooled = _anchor_counts(pooled) if pooled else (n_own, rate_own)
    n_evidence = n_pooled + (pooled["realized"]["n"] if pooled else n_real)
    rate = rate_pooled if rate_pooled is not None else rate_own
    detail = {"n": n_evidence, "n_instrument": n_own, "n_realized": n_real, "rate": rate}

    if n_evidence < _CONF_MEDIUM_N:
        return {**detail, "level": "low",
                "reason": f"only {n_evidence} settled outcome(s) — a rate this thin is not evidence"}
    if n_evidence < _CONF_HIGH_N:
        return {**detail, "level": "medium",
                "reason": (f"{n_evidence} settled outcomes, hit-rate {_pct(rate)} — "
                           "indicative, not conclusive")}
    # Instability caveat, checked before the chance band: an instrument whose own
    # rate disagrees sharply with the pooled one is a small, possibly
    # regime-specific sample, and that is the more specific diagnosis.
    if (pooled is not None and n_own >= 5 and rate_own is not None and rate_pooled is not None
            and abs(rate_own - rate_pooled) > _CONF_DISAGREEMENT):
        return {**detail, "level": "medium",
                "reason": (f"{n_evidence} settled outcomes pooled, but this instrument's "
                           f"own rate ({_pct(rate_own)} of {n_own}) disagrees with the "
                           f"pooled one ({_pct(rate_pooled)}) — treat as unstable")}
    in_band = rate is not None and _CONF_FLAT_BAND[0] <= rate <= _CONF_FLAT_BAND[1]
    if in_band:
        return {**detail, "level": "medium",
                "reason": (f"{n_evidence} settled outcomes, hit-rate {_pct(rate)} — "
                           "indistinguishable from chance, so the record does not "
                           "support leaning either way")}
    return {**detail, "level": "high",
            "reason": f"{n_evidence} settled outcomes, hit-rate {_pct(rate)}"}


def _pct(rate) -> str:
    return "n/a" if rate is None else f"{rate * 100:.0f}%"


def render_calibration(calib, ticker, max_horizons=4) -> str:
    """One compact line for context injection; empty when nothing has settled."""
    if not calib:
        return ""

    def _fmt(pack):
        bits = []
        rz = pack.get("realized")
        if rz and rz.get("n"):
            bits.append(f"realized {rz['hits']}/{rz['n']}")  # ground truth first
        if pack.get("by_horizon"):
            bits += [f"{h}d {d['hits']}/{d['n']}"
                     for h, d in sorted(pack["by_horizon"].items())[:max_horizons]]
        if not bits:
            return "no outcomes yet"
        return " ".join(bits)

    t, c = calib["ticker"], calib["cross"]
    if t["n"] == 0 and c["n"] == 0:
        return ""
    conf = calib.get("confidence") or c.get("confidence") or {}
    label = conf.get("level")
    prefix = "CALIBRATION"
    if label:
        prefix += f" (memory confidence: {label} — {conf.get('reason')})"
    parts = []
    if t["n"]:
        parts.append(f"{ticker} {t['n']} calls: {_fmt(t)}")
    if c["n"]:
        parts.append(f"all tickers {c['n']} calls: {_fmt(c)}")
    tail = ""
    if label == "low":
        tail = (". Treat this record as anecdote: do not let it override the current "
                "evidence, and do not cite it as a track record.")
    return prefix + " — " + " | ".join(parts) + tail
