# SHADOW paper strategies: every new main paper buy is mirrored at the same entry/size into each strategy's own CSV.
# Exits are judged on GeckoTerminal 1-minute candles since the last check (high/low, SL first if both in one candle),
# falling back to the DexScreener snapshot price (marked price_source=snapshot) when candles are unavailable.
# Paper only: nothing here can trade.
import csv, os, time
from datetime import datetime, timezone
from config import SCANNER as S, SHADOW
import paper, candles, dexscreener as dx

FIELDS = paper.FIELDS + ["strategy", "last_candle_ts", "price_source"]

def _load(path):
    if not os.path.exists(path): return []
    with open(path, newline="") as f: return list(csv.DictReader(f))
def _save(rows, path):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, path)

def open_all(mint, symbol, pair, price, confidence, why):
    opened = []
    for name, st in SHADOW.items():
        rows = _load(st["file"])
        if any(r["mint"] == mint and r["status"] == "open" for r in rows): continue
        usd = float(S["paper_ticket_usd"]); tokens = usd * (1 - paper._fee()) / price; t = paper.now()
        r = {"id": str(len(rows) + 1), "opened_at": paper.iso(t), "mint": mint, "symbol": symbol, "pair": pair,
             "entry_price": repr(price), "entry_usd": usd, "tokens_initial": repr(tokens), "tokens_left": repr(tokens),
             "half_taken": "0", "status": "open", "realized_usd": 0, "last_price": repr(price), "last_update": paper.iso(t),
             "closed_at": "", "exit_reason": "", "events": f"{paper.iso(t)} BUY ${usd} @ {price}", "confidence": confidence,
             "why": why, "strategy": name, "last_candle_ts": str(int(t.timestamp())), "price_source": ""}
        paper._recalc(r); rows.append(r); _save(rows, st["file"]); opened.append(name)
    return opened

def _close(r, price, reason, when, src):
    paper._sell(r, float(r["tokens_left"]), price, f"CLOSE_{reason}[{src}]")
    r["tokens_left"] = "0"; r["status"] = "closed"; r["exit_reason"] = reason
    r["closed_at"] = paper.iso(when); r["last_price"] = repr(price); r["price_source"] = src

def update_all(sleep_s=1.0):
    events, cache, snap = [], {}, None
    now = paper.now(); now_ts = int(now.timestamp())
    need = {}   # (pool, mint) -> earliest candle time any open shadow row still needs (one fetch per coin per scan)
    for st in SHADOW.values():
        for r in _load(st["file"]):
            if r["status"] != "open": continue
            o = int(datetime.fromisoformat(r["opened_at"]).timestamp())
            k = (r["pair"], r["mint"]); need[k] = min(need.get(k, 10**12), max(int(r.get("last_candle_ts") or o), o))
    for name, st in SHADOW.items():
        rows = _load(st["file"]); opn = [r for r in rows if r["status"] == "open"]
        if not opn: continue
        for r in opn:
            entry = float(r["entry_price"]); opened_ts = int(datetime.fromisoformat(r["opened_at"]).timestamp())
            deadline = opened_ts + int(st["max_hold_min"] * 60)
            since = max(int(r.get("last_candle_ts") or opened_ts), opened_ts)
            hit, cs, src = None, None, "candles_1m"
            try:
                key = (r["pair"], r["mint"])
                if key not in cache:
                    cache[key] = candles.ohlcv(r["pair"], r["mint"], need.get(key, opened_ts)); time.sleep(sleep_s)
                # first candle may start before entry: only use candles that START after the entry minute
                cs = [c for c in cache[key] if c[0] >= since and c[0] >= opened_ts - opened_ts % 60 + 60]
                hit = candles.first_exit(cs, entry, st["tp_pct"], st["sl_pct"], deadline)
                if cs:
                    r["last_candle_ts"] = str(int(cs[-1][0])); r["last_price"] = repr(cs[-1][4])
            except Exception as e:
                cs = None; src = "snapshot"
                events.append(f"[{name}] {r['symbol']}: candles unavailable ({type(e).__name__}), using snapshot price")
            if not hit and cs and now_ts >= deadline:   # max hold reached with no later trade: exit at last close
                hit = ("time_exit", cs[-1][4], now_ts)
            if hit:
                reason, px, ts = hit
                _close(r, px, reason, datetime.fromtimestamp(ts, timezone.utc), src)
                events.append(f"[{name}] {r['symbol']}: closed ({reason}) at {100 * (px / entry - 1):+.1f}% [{src}]")
            else:
                if cs is None or (not cs and now_ts >= deadline):   # snapshot fallback (or no trades at all)
                    if snap is None:
                        try: snap = dx.all_pairs_for(list({x["mint"] for s2 in SHADOW.values() for x in _load(s2["file"]) if x["status"] == "open"}), sleep_s)
                        except Exception as e: snap = {}; events.append(f"[{name}] snapshot price error: {e}")
                    ps = snap.get(r["mint"]) or []
                    p = next((x for x in ps if x.get("pairAddress") == r["pair"]), None) or (ps[0] if ps else None)
                    if p and p.get("priceUsd"):
                        px = float(p["priceUsd"]); chg = 100 * (px / entry - 1); r["last_price"] = repr(px); src = "snapshot"
                        reason = "stop_loss" if chg <= st["sl_pct"] else "take_profit" if chg >= st["tp_pct"] else \
                                 "time_exit" if now_ts >= deadline else None
                        if reason:
                            _close(r, px, reason, now, src)
                            events.append(f"[{name}] {r['symbol']}: closed ({reason}) at {chg:+.1f}% [snapshot]")
                    r["price_source"] = src
                else:
                    r["price_source"] = src
            r["last_update"] = paper.iso(now); paper._recalc(r)
        _save(rows, st["file"])
    return events
