# One-off (re-runnable) BACKFILL of the LAUNCH 'flash' paper strategy (+10% / -7% / 15 min) for launch buys made before
# flash existed. Same entries as launch_scalp.csv (time, price, $10) - no new buy logic. Walks historical GeckoTerminal
# 1-min candles of the original curve pool (plus the post-graduation pool if the coin graduated). Rows are marked
# price_source=candles_1m_backfill and "BACKFILL" in events. Read-only public API + CSV. Paper only.
# 1-min candles can't capture sub-minute moves, so real fills on fast exits would differ.
import csv, os, sys, time
from datetime import datetime, timezone
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from config import LAUNCH as L
import launch, paper, candles

def main():
    tp, fr, sl, mh = L["strategies"]["flash"]; path = L["paper_files"]["flash"]
    fl = launch._load(path); have = {r["mint"] for r in fl}
    orig_pair = {}
    for c in launch._load(L["check_file"]):
        if c.get("paper_opened") == "1" and c.get("pair"): orig_pair.setdefault(c["mint"], c["pair"])
    done = 0
    for s in sorted(launch._load(L["paper_files"]["scalp"]), key=lambda r: r["opened_at"]):
        if s["mint"] in have: continue
        o = int(datetime.fromisoformat(s["opened_at"]).timestamp()); deadline = o + int(mh * 60)
        if time.time() < deadline + 120: continue                     # too recent: launch.py handles it live
        price = float(s["entry_price"]); usd = float(s["entry_usd"]); tokens = usd / price
        pair0 = orig_pair.get(s["mint"], s["pair"])
        r = {k: s.get(k, "") for k in launch.PAPER_FIELDS}
        r.update({"id": str(len(fl) + 1), "pair": pair0, "dex": "pumpfun", "graduated_at": "", "tokens_initial": repr(tokens),
                  "tokens_left": repr(tokens), "half_taken": "0", "status": "open", "realized_usd": 0, "last_price": repr(price),
                  "closed_at": "", "exit_reason": "", "strategy": "flash", "price_source": "candles_1m_backfill",
                  "events": f"{s['opened_at']} BUY ${usd} @ {price} (launch, bonding curve; BACKFILL from scalp entry {launch.iso()})"})
        start = o - o % 60 + 60; until = deadline + 60
        try:
            cs = candles.ohlcv(pair0, s["mint"], start, until); time.sleep(2.5)
            if s["pair"] != pair0:                                     # graduated later: add the new pool's candles too
                last = cs[-1][0] if cs else start - 1
                cs2 = candles.ohlcv(s["pair"], s["mint"], start, until); time.sleep(2.5)
                cs += [c for c in cs2 if c[0] > last]
        except Exception as e:
            print(f"skip {s['symbol']} {s['mint'][:8]}: candles unavailable ({type(e).__name__})"); time.sleep(3); continue
        launch.walk(r, cs, tp, fr, sl, deadline)
        last_c = [c for c in cs if c[0] < deadline]
        if r["status"] == "open":                                      # no TP/SL before 15 min: sell at last close before deadline
            px = last_c[-1][4] if last_c else price; when = datetime.fromtimestamp(deadline, timezone.utc)
            launch._sell(r, float(r["tokens_left"]), px, "CLOSE_time_exit", when); launch._close(r, "time_exit", when)
        if not cs: r["events"] += " | no trades in window (exit at entry price)"
        r["last_candle_ts"] = str(int(cs[-1][0])) if cs else str(o)
        r["last_price"] = repr(float(r["realized_usd"]) / float(r["tokens_initial"])); r["last_update"] = launch.iso()
        paper._recalc(r); fl.append(r); have.add(s["mint"]); done += 1
        print(f"{s['symbol']:<12} {r['exit_reason']:<11} {float(r['pnl_usd']):+.2f}  ({len(cs)} candles)")
    launch._save(fl, path); print(f"backfilled {done}; flash book now {len(fl)} rows")

if __name__ == "__main__": main()
