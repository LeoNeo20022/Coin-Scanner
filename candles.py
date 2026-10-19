# Free GeckoTerminal OHLCV (no key, ~30 calls/min). Read-only price history for paper exits.
import time, requests
API = "https://api.geckoterminal.com/api/v2/networks/solana"
H = {"Accept": "application/json"}

def _get(url, params, tries=3):
    for i in range(tries):
        r = requests.get(url, params=params, headers=H, timeout=15)
        if r.status_code == 429: time.sleep(5 * (i + 1)); continue
        r.raise_for_status(); return r.json()
    raise RuntimeError("geckoterminal 429 rate limited")

def top_pool(mint):
    """Most liquid pool for a token, or None."""
    d = _get(f"{API}/tokens/{mint}/pools", {"page": 1}).get("data") or []
    d.sort(key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0), reverse=True)
    return d[0]["id"].split("_", 1)[1] if d else None

def ohlcv(pool, mint, since_ts, until_ts=None, timeframe="minute", aggregate=1, max_pages=10):
    """Candles [ts, o, h, l, c, v] (USD price of `mint`) with since_ts <= ts <= until_ts, oldest first.
    Minutes without trades have no candle."""
    until_ts = int(until_ts or time.time()); out = {}; before = until_ts + 60 * aggregate
    for _ in range(max_pages):
        d = _get(f"{API}/pools/{pool}/ohlcv/{timeframe}",
                 {"aggregate": aggregate, "limit": 1000, "before_timestamp": before, "currency": "usd", "token": mint})
        lst = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        if not lst: break
        for c in lst:
            if since_ts <= c[0] <= until_ts: out[c[0]] = [float(x) for x in c]
        oldest = min(c[0] for c in lst)
        if oldest <= since_ts or len(lst) < 1000: break
        before = oldest
    return [out[k] for k in sorted(out)]

def first_exit(candles, entry, tp_pct, sl_pct, deadline_ts=None):
    """Walk candles oldest first. Returns (reason, exit_price, candle_ts) or None if still open.
    Same candle touching both: stop loss first (conservative). SL fills at the stop price, or the candle
    open if it already gapped below; TP fills at the target price. Past deadline_ts: exit at that candle's close."""
    tp, sl = entry * (1 + tp_pct / 100), entry * (1 + sl_pct / 100)
    for ts, o, h, l, c, _v in candles:
        if deadline_ts and ts >= deadline_ts: return ("time_exit", c, ts)
        if l <= sl: return ("stop_loss", min(sl, o), ts)
        if h >= tp: return ("take_profit", max(tp, o) if o >= tp else tp, ts)
    return None
