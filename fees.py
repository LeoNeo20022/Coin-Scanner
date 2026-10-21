# Estimated LIVE fees for paper trades (display only; paper numbers and exit logic are unchanged).
# Assumes live trades go through Jupiter direct: per side = pool fee + Jupiter platform fee + network fee.
#   buy : tokens = ((E - net) * (1 - jup_buy)) * (1 - pool) / price      -> scale k on everything the paper trade sold
#   sell: each sale's gross proceeds x -> x * (1 - pool) * (1 - jup_sell) - net
# Open positions are marked as if sold now (one sell). The hold strategy's half-at-target counts as an extra sell.
# Jupiter's fee is 0.50% instead of 0.10% while the token is < 24h old (age at the time of that side, approx from
# the pair creation time / logged ages). Gross paper value = entry_usd + pnl_usd (paper is simulated with no fees).
import csv, json, os, time, requests
from datetime import datetime
from config import FEES as F

def _ts(s):
    try: return datetime.fromisoformat(s).timestamp()
    except Exception: return None

def jup_pct(created_ts, at_ts):
    if created_ts is None or at_ts is None: return F["jup_new_token_pct"]        # unknown age -> assume new (conservative)
    return F["jup_new_token_pct"] if at_ts - created_ts < F["new_token_hours"] * 3600 else F["jup_pct"]

def after_fees(row, pool_pct, created_ts=None, now_ts=None):
    """After-fee P&L (USD) for one paper row."""
    E = float(row["entry_usd"]); gross = E + float(row["pnl_usd"] or 0)
    net = F["network_usd_per_side"]; pool = pool_pct / 100
    t_open = _ts(row["opened_at"]); t_sell = _ts(row.get("closed_at") or "") or now_ts or time.time()
    jb, js = jup_pct(created_ts, t_open) / 100, jup_pct(created_ts, t_sell) / 100
    k = max(0.0, (1 - net / E) * (1 - jb)) * (1 - pool)
    n_sells = 1 + (1 if str(row.get("half_taken")) == "1" else 0)
    return round(gross * k * (1 - pool) * (1 - js) - n_sells * net - E, 4)

class Book:
    """Looks up pool fee (by dex) and token creation time for each row's pair/mint. One batched DexScreener call
    for unknown pairs (read-only); falls back to the default pool fee if it fails."""
    def __init__(self, rows_by_file=(), extra_logs=True):
        self.dex, self.created = {}, {}
        if extra_logs: self._from_logs()
        pairs = {r["pair"] for rows in rows_by_file for r in rows if r.get("pair")}
        for r in (r for rows in rows_by_file for r in rows):
            if r.get("dex"): self.dex[r["pair"]] = r["dex"]
        need = [p for p in pairs if p not in self.dex]
        for i in range(0, len(need), 30):
            try:
                d = requests.get("https://api.dexscreener.com/latest/dex/pairs/solana/" + ",".join(need[i:i + 30]), timeout=10).json()
                for p in d.get("pairs") or []:
                    self.dex[p["pairAddress"]] = p.get("dexId")
                    m = (p.get("baseToken") or {}).get("address"); c = p.get("pairCreatedAt")
                    if m and c: self._created(m, c / 1000)
            except Exception: pass
    def _created(self, mint, ts):
        if ts and (mint not in self.created or ts < self.created[mint]): self.created[mint] = ts
    def _from_logs(self):
        for path, col, mult in (("scanner_log.csv", "age_hours", 3600), ("second_look.csv", "age_hours", 3600),
                                ("launch_checks.csv", "age_min", 60)):
            if not os.path.exists(path): continue
            with open(path, newline="") as f:
                for r in csv.DictReader(f):
                    t = _ts(r.get("time") or "")
                    try: age = float(r.get(col) or "")
                    except ValueError: continue
                    if t: self._created(r["mint"], t - age * mult)
    def pool_pct(self, row, launch=False):
        if launch: return F["launch_pool_pct"]
        return F["pool_pct_by_dex"].get(self.dex.get(row.get("pair")), F["pool_pct_default"])
    def pnl(self, rows, launch=False):
        return sum(after_fees(r, self.pool_pct(r, launch), self.created.get(r["mint"])) for r in rows)
