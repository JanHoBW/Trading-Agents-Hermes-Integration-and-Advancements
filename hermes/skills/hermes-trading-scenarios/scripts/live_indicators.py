#!/usr/bin/env python3
"""Live technical indicators for a ticker (Yahoo chart API, stdlib only).

Usage:
  live_indicators.py TICKER [--range 3mo] [--days 5]

Prints an OHLCV table for the last N sessions plus RSI(14, Wilder), MACD 12/26/9,
SMA20/50 and EMA10 computed from daily closes including today's live close.
Today's row is LIVE until market close (Xetra 17:30 CEST) — label it intraday before then.
"""
import datetime
import json
import sys
import urllib.request


def fetch(ticker, rng="3mo"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={rng}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    rows = []
    for t, o, h, l, c, v in zip(ts, q["open"], q["high"], q["low"], q["close"], q["volume"]):
        if c is None:
            continue
        day = datetime.datetime.fromtimestamp(t, datetime.UTC).strftime("%Y-%m-%d")
        rows.append((day, o, h, l, c, v))
    return rows


def ema(vals, n):
    k = 2 / (n + 1)
    e = vals[0]
    out = []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi_series(closes, n=14):
    """Wilder-smoothed RSI; entry i aligns with closes[i] (None until enough history)."""
    out = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    ticker = args[0]
    rng, days = "3mo", 5
    if "--range" in args:
        rng = args[args.index("--range") + 1]
    if "--days" in args:
        days = int(args[args.index("--days") + 1])

    rows = fetch(ticker, rng)
    closes = [r[4] for r in rows]
    n = len(rows)
    if n < 60:
        print(f"WARNING: only {n} sessions fetched — SMA50/RSI/MACD may be unreliable (use --range 6mo)")

    e12, e26 = ema(closes, 12), ema(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd, 9)
    hist = [m - s for m, s in zip(macd, sig)]
    rsi = rsi_series(closes)

    print(f"{ticker} — as of {rows[-1][0]} (today's row live until market close)")
    print("date        open     high      low    close   volume   delta")
    for i in range(n - days, n):
        d, o, h, l, c, v = rows[i]
        prev = closes[i - 1] if i > 0 else None
        delta = f"{(c / prev - 1) * 100:+.2f}%" if prev else "   --"
        vv = v if v is not None else 0
        print(f"{d}  {o:8.2f} {h:8.2f} {l:8.2f} {c:8.2f} {vv:>9,.0f}  {delta}")

    i = n - 1
    sma20 = sum(closes[-20:]) / min(20, len(closes))
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else float("nan")
    e10 = ema(closes, 10)[i]
    print()
    print(f"RSI14={rsi[i]:.1f}  MACD={macd[i]:.2f}  signal={sig[i]:.2f}  hist={hist[i]:.2f}")
    print(f"SMA20={sma20:.2f}  SMA50={sma50:.2f}  EMA10={e10:.2f}")


if __name__ == "__main__":
    main()
