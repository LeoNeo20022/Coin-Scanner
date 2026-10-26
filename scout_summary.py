# Plain summary of the SCOUT paper sources (FORWARD, see scout.py): pool-graduated filter + WAIT-3 pullback. Read-only.
# Usage: python3 scout_summary.py [--hours 24]
import csv, os, sys
from collections import Counter
from datetime import datetime, timezone, timedelta
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from config import SCOUT as C

def rows(p):
    if not os.path.exists(p): return []
    with open(p, newline="") as f: return list(csv.DictReader(f))
def money(x): return f"{'-' if x < 0 else '+'}${abs(x):.2f}"
LABEL = {"pool": "POOL FILTER (@JnoAYx)", "wait3": "WAIT-3 PULLBACK (@malangeeSOL)"}

def main(hours=24):
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    print(f"\n=== SCOUT strategies, FORWARD paper ($10/buy, pretend ${C['budget_usd']} each; after fees = pool fee/side + $0.003, no Jupiter fee, no slippage) ===")
    for src, path in C["trade_files"].items():
        R = rows(path)
        if not R: print(f"{LABEL[src]}: no paper buys yet"); continue
        first = min(datetime.fromisoformat(r["opened_at"]) for r in R)
        days = max(1.0, (datetime.now(timezone.utc) - first).total_seconds() / 86400)
        n_coins = len({(r["mint"], r["opened_at"]) for r in R})
        new = len({(r["mint"], r["opened_at"]) for r in R if datetime.fromisoformat(r["opened_at"]) >= cut})
        print(f"{LABEL[src]}: {n_coins} paper buys since {first:%b %d %H:%M} UTC ({new} in the last {hours}h)")
        for s in dict.fromkeys(r["strategy"] for r in R):
            S = [r for r in R if r["strategy"] == s]
            pn = sum(float(r["pnl_usd"] or 0) for r in S); fe = sum(float(r["pnl_after_fee_usd"] or 0) for r in S)
            w = sum(1 for r in S if float(r["pnl_after_fee_usd"] or 0) > 0); op = sum(1 for r in S if r["status"] == "open")
            inuse = op * C["ticket_usd"]
            print(f"  {s:10s} buys {len(S):3d} open {op:2d} wins {w:3d} ({100 * w / len(S):3.0f}%) paper {money(pn)} after fees {money(fe)}"
                  f"  ({money(fe / days)}/day) | ${C['budget_usd']} -> ${C['budget_usd'] + fe:.2f}, ${inuse} in open trades")
    ck = rows(C["check_file"])
    if ck:
        recent = [r for r in ck if datetime.fromisoformat(r["time"]) >= cut]
        for lab, L in (("all time", ck), (f"last {hours}h", recent)):
            fails = Counter(f"{r['stage']}:{r['reason'].split(' ')[0]}" for r in L if r["result"] == "fail")
            liq = sum(1 for r in L if r["stage"] == "liquidity"); saf = sum(1 for r in L if r["stage"] == "safety")
            print(f"  checks {lab}: {len(L)} attempts, opened {sum(1 for r in L if r['stage'] == 'opened')}, "
                  f"liquidity-check rejects {liq}, safety rejects {saf}" + (f" | {', '.join(f'{k} {v}' for k, v in fails.most_common(6))}" if fails else ""))

if __name__ == "__main__":
    h = int(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 24
    main(h)
