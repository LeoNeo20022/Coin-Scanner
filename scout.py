# SCOUT STRATEGIES (paper / log only, added Oct 26 2026). Two strategies scouted on X by Theo, see config.SCOUT:
#   A "pool"  POOL-GRADUATED FILTER (@JnoAYx)   -> pool_filter_trades.csv
#   B "wait3" WAIT-3 PULLBACK (@malangeeSOL)    -> wait3_trades.csv
# Every attempted paper buy (pass or fail, with the reason) -> scout_checks.csv. State -> scout_state.json.
# Nothing here can trade: it reads free public APIs (GeckoTerminal, DexScreener, RugCheck/fallback, a Jupiter QUOTE
# with no wallet) and rewrites CSV/JSON files. No Grok calls. The pure functions (qualifies_a, wait3_signal, Pos/step,
# money) are also imported by /workspace/copyfilter/scout_backtest.py so the backtest uses the same rules.
import csv, json, os, sys, time, traceback
from datetime import datetime, timezone
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)
from config import SCOUT as C

SOL = "So11111111111111111111111111111111111111112"
SKIP_BASE = {SOL, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
TRADE_FIELDS = ["id", "opened_at", "source", "strategy", "mint", "symbol", "pair", "dex", "pool_pct", "entry_price",
                "entry_usd", "stop_price", "deadline", "tp_done", "left_frac", "legs", "last_price", "last_candle_ts",
                "last_update", "status", "closed_at", "exit_reason", "pnl_usd", "pnl_after_fee_usd", "price_source", "entry_note"]
CHECK_FIELDS = ["time", "source", "mint", "symbol", "pair", "dex", "price_usd", "mcap_usd", "liquidity_usd", "vol24_usd",
                "pool_age_h", "stage", "result", "reason", "safety_source", "sell_quote_usd", "sell_back_usd",
                "sell_loss_pct", "jup_impact_pct", "note"]

# ---------------- exits (name -> take-profits [(pct, fraction of ORIGINAL size)], stop % from entry or 'setup_low', max hold min)
EXITS = {
    "pool": {"tp50_sl30": ([(50, 1.0)], -30, 72 * 60),       # his +50% exit, -30% stop
             "target5":   ([(50, 1.0)], -50, 72 * 60),       # = his +50% exit with a -50% stop (our target5)
             "hold":      ([(100, 0.5)], -50, 72 * 60),      # our main: half at +100%, -50% stop, 72h
             "scalp":     ([(20, 1.0)], -10, 60)},
    "wait3": {"wait3_24h": ([(100, 0.5), (400, 0.25), (900, 0.25)], "setup_low", 24 * 60),   # his ladder, 24h time stop
              "wait3_72h": ([(100, 0.5), (400, 0.25), (900, 0.25)], "setup_low", 72 * 60),   # same, 72h time stop
              "hold":      ([(100, 0.5)], -50, 72 * 60),
              "target5":   ([(50, 1.0)], -50, 72 * 60),
              "scalp":     ([(20, 1.0)], -10, 60)},
}

def now_ts(): return int(time.time())
def iso(ts=None): return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).isoformat(timespec="seconds")
def say(msg): print(f"[{iso()}] scout: {msg}", flush=True)
def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None
def pool_pct(dex): return C["pool_pct_by_dex"].get(dex or "", C["pool_pct_default"])

# ---------------- pure rules ----------------
def snapshot(mint, p):
    """DexScreener pair -> the fields the rules need."""
    vol, liq = p.get("volume") or {}, (p.get("liquidity") or {}).get("usd")
    mcap = p.get("marketCap") if p.get("marketCap") is not None else p.get("fdv")
    created = p.get("pairCreatedAt")
    return {"mint": mint, "symbol": ((p.get("baseToken") or {}).get("symbol") or "").strip()[:20], "dex": p.get("dexId"),
            "pair": p.get("pairAddress"), "price_usd": num(p.get("priceUsd")), "mcap_usd": mcap, "liquidity_usd": liq,
            "vol24_usd": vol.get("h24"), "vol1_usd": vol.get("h1"),
            "created_ts": int(created / 1000) if created else None,
            "pool_age_h": round((time.time() * 1000 - created) / 3.6e6, 2) if created else None}

def qualifies_a(m):
    """Strategy A market filter. Returns None if it qualifies, else the reason."""
    if m.get("dex") == "pumpfun" or not m.get("liquidity_usd"): return "not_graduated"       # still on the bonding curve
    for k in ("price_usd", "mcap_usd", "vol24_usd", "pool_age_h"):
        if m.get(k) is None: return f"missing:{k}"
    if m["liquidity_usd"] < C["a_min_liquidity_usd"]: return "liquidity_low"
    if m["pool_age_h"] * 60 < C["a_min_pool_age_min"]: return "too_new"
    if m["vol24_usd"] < C["a_min_vol24_usd"]: return "vol24_low"
    if m["mcap_usd"] < C["a_min_mcap_usd"]: return "mcap_too_low"
    if m["mcap_usd"] > C["a_max_mcap_usd"]: return "mcap_too_high"
    return None

def prefilter_b(m):
    if m.get("dex") == "pumpfun" or not m.get("liquidity_usd"): return "not_graduated"
    if m.get("pool_age_h") is None or m.get("price_usd") is None: return "missing"
    if m["pool_age_h"] > C["b_max_pool_age_h"]: return "too_old"
    if m["pool_age_h"] * 60 < C["b_min_pool_age_min"]: return "too_new"
    if m["liquidity_usd"] < C["b_min_liquidity_usd"]: return "liquidity_low"
    if (m.get("vol24_usd") or 0) < C["b_min_vol24_usd"]: return "vol24_low"
    if (m.get("mcap_usd") or 0) < C["b_min_mcap_usd"]: return "mcap_too_low"
    return None

def wait3_signals(cs, pool_created_ts=None, P=None):
    """WAIT-3 on 5-min candles [ts,o,h,l,c,v] from the pool start, oldest first. Yields every entry signal (dict).
    Rules (our reading of @malangeeSOL's post; parameters in config.SCOUT['w3']):
      ref  = close of the pool's first 5-min candle ('launch'); skip for good once the high reaches max_run_x (5x) of it
      H    = running high; a new high resets the setup
      pull = low falls 30-50% below H (more than 50% = broken until a new high)
      bounce: a close >= +10% above the pullback low L
      higher low: after the bounce, a dip of >= 5% from the bounce peak whose low L2 stays >= 2% above L
      entry: the first close above the high of the L2 candle, while still <= 85% of H, with the last hour's volume >= 50%
             of the hour before the high and >= $5K. Stop = L (the pullback low)."""
    P = P or C["w3"]
    if not cs: return
    ref = cs[0][4]; H = None; st = "run"; L = L2 = L2h = B = None; hiidx = 0
    for i, (t, o, h, l, c, v) in enumerate(cs):
        if H is not None and H >= P["max_run_x"] * ref: return           # already up 5x from launch: skip the coin
        if H is None or h > H:
            H, hiidx, st, L, L2, L2h, B = h, i, "run", None, None, None, None
            if H >= P["max_run_x"] * ref: return
            continue
        if st == "broken": continue
        if st in ("run", "pulled"):
            L = l if L is None else min(L, l)
            dd = 1 - L / H
            if dd > P["pull_max"]: st = "broken"; continue
            if dd >= P["pull_min"]: st = "pulled"
            if st == "pulled" and c >= L * (1 + P["bounce_pct"] / 100): st, B = "bounce", h
            continue
        if st in ("fired", "broken_up"): continue                     # wait for a new high to re-arm
        # bounce / higher-low phase
        if l <= L:                                                    # lower low: back to the pullback
            L = l; L2 = L2h = None
            st = "broken" if 1 - L / H > P["pull_max"] else "pulled"; continue
        B = max(B, h)
        if L2 is None or l < L2:
            if l <= B * (1 - P["min_dip_pct"] / 100) and l >= L * (1 + P["hl_min_pct"] / 100):
                L2, L2h = l, h
            continue
        if L2h is not None and c > L2h:
            win = cs[max(0, i - 11):i + 1]; v1 = sum(x[5] for x in win)
            pre = cs[max(0, hiidx - 11):hiidx + 1]; v0 = sum(x[5] for x in pre)
            ok_vol = v1 >= P["min_vol_1h_usd"] and v1 >= P["vol_hold_ratio"] * v0
            if c <= P["max_entry_vs_high"] * H and ok_vol:
                yield {"ts": t + 300, "entry": c, "stop": L, "high": H, "higher_low": L2, "ref": ref,
                       "vol_1h": round(v1), "vol_1h_before_high": round(v0),
                       "pool_age_h": round((t + 300 - pool_created_ts) / 3600, 2) if pool_created_ts else None}
                st = "fired"            # one signal per setup; a new high re-arms it
            elif c > P["max_entry_vs_high"] * H:
                st = "broken_up"        # ran back up without a clean entry; wait for a new high
    return

# ---------------- paper position (shared by forward + backtest) ----------------
def new_pos(entry, opened_ts, spec, setup_low=None):
    tps, sl, hold_min = spec
    stop = setup_low if sl == "setup_low" else entry * (1 + sl / 100)
    return {"entry": entry, "stop": stop, "tps": tps, "tp_done": 0, "left": 1.0, "legs": [], "status": "open",
            "deadline": opened_ts + hold_min * 60, "last": entry, "last_ts": opened_ts, "exit_reason": "", "exit_ts": None}

def step(pos, cs):
    """Advance over candles (only those that START after entry / the last processed one). Stop first if a candle
    touches both; stops fill at min(stop, open); targets fill exactly at the target (no gap-up bonus)."""
    e = pos["entry"]
    for row in cs:
        t, o, h, l, c = row[:5]
        if pos["status"] != "open": break
        if t >= pos["deadline"]: _close(pos, c / e, t, "time_exit"); break
        if l <= pos["stop"]: _close(pos, min(pos["stop"], o) / e, t, "stop_loss"); break
        while pos["tp_done"] < len(pos["tps"]):
            pct, frac = pos["tps"][pos["tp_done"]]
            if h < e * (1 + pct / 100): break
            sell = min(frac, pos["left"]); pos["legs"].append((round(sell, 6), 1 + pct / 100))
            pos["left"] = round(pos["left"] - sell, 6); pos["tp_done"] += 1
            if pos["left"] <= 1e-9: _close(pos, None, t, "take_profit"); break
        pos["last"], pos["last_ts"] = c, t
    return pos

def _close(pos, mult, t, why):
    if mult is not None and pos["left"] > 0: pos["legs"].append((pos["left"], mult))
    pos.update(left=0.0, status="closed", exit_reason=why, exit_ts=t)

def money(legs, pct, size=None, net=None):
    """(paper P&L, after-fee P&L) for a buy of `size` USD; each sell leg pays pool fee + network, the buy pays both."""
    size = size or C["ticket_usd"]; net = C["network_usd_per_side"] if net is None else net; p = pct / 100
    paper = size * (sum(f * m for f, m in legs) - 1)
    k = (size - net) * (1 - p)
    fee = sum(k * f * m * (1 - p) - net for f, m in legs) - size
    return round(paper, 4), round(fee, 4)

def pos_pnl(pos, pct):
    legs = list(pos["legs"]) + ([(pos["left"], pos["last"] / pos["entry"])] if pos["left"] > 1e-9 else [])
    return money(legs, pct)

# ---------------- I/O helpers ----------------
def load_csv(path):
    if not os.path.exists(path): return []
    with open(path, newline="") as f: return list(csv.DictReader(f))
def save_csv(rows, path, fields):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in fields})
    os.replace(tmp, path)
def append_check(row):
    new = not os.path.exists(C["check_file"])
    with open(C["check_file"], "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CHECK_FIELDS)
        if new: w.writeheader()
        w.writerow({k: row.get(k, "") for k in CHECK_FIELDS})
def load_state():
    try: s = json.load(open(C["state_file"]))
    except Exception: s = {}
    for k in ("watch", "evals", "cool", "decimals", "w3_seen"): s.setdefault(k, {})
    s.setdefault("bought", {"pool": {}, "wait3": {}}); s.setdefault("days", {})
    return s
def save_state(s):
    t = now_ts(); keep = C["watch_hours"] * 3600
    s["watch"] = dict(sorted(((k, v) for k, v in s["watch"].items() if t - v["t"] < keep), key=lambda kv: -kv[1]["t"])[:C["max_watch"]])
    s["evals"] = {k: v for k, v in s["evals"].items() if k in s["watch"]}
    s["cool"] = {k: v for k, v in s["cool"].items() if v > t}
    s["w3_seen"] = {k: v for k, v in s["w3_seen"].items() if t - v < 7 * 86400}
    for src in s["bought"]: s["bought"][src] = {k: v for k, v in s["bought"][src].items() if t - v < 30 * 86400}
    s["days"] = dict(sorted(s["days"].items())[-10:])
    if len(s["decimals"]) > 3000: s["decimals"] = dict(list(s["decimals"].items())[-2000:])
    tmp = C["state_file"] + ".tmp"; json.dump(s, open(tmp, "w"), indent=1); os.replace(tmp, C["state_file"])

def row_to_pos(r):
    return {"entry": float(r["entry_price"]), "stop": float(r["stop_price"]), "tps": json.loads(r["_tps"]) if r.get("_tps") else None,
            "tp_done": int(r["tp_done"] or 0), "left": float(r["left_frac"]), "status": r["status"],
            "legs": [tuple(map(float, x.split("@"))) for x in r["legs"].split(";") if x] if r.get("legs") else [],
            "deadline": int(r["deadline"]), "last": float(r["last_price"]), "last_ts": int(r["last_candle_ts"]),
            "exit_reason": r.get("exit_reason", ""), "exit_ts": None}
def pos_to_row(pos, r, src):
    r.update(tp_done=pos["tp_done"], left_frac=pos["left"], legs=";".join(f"{f:g}@{m:g}" for f, m in pos["legs"]),
             last_price=repr(pos["last"]), last_candle_ts=str(int(pos["last_ts"])), status=pos["status"])
    if pos["status"] == "closed" and not r.get("closed_at"):
        r["closed_at"] = iso(pos["exit_ts"]); r["exit_reason"] = pos["exit_reason"]
    paper, fee = pos_pnl(pos, float(r["pool_pct"])); r["pnl_usd"], r["pnl_after_fee_usd"] = paper, fee

# ---------------- network (read-only) ----------------
def _gt(path, params=None):
    import candles
    return candles._get(f"{candles.API}/{path}" if not path.startswith("http") else path, params or {})

def gt_feed_mints():
    out, errs = {}, []
    import requests
    for feed in C["gt_feeds"]:
        try:
            d = requests.get(f"https://api.geckoterminal.com/api/v2/networks/solana/{feed}", headers={"Accept": "application/json"},
                             timeout=15)
            if d.status_code == 429: errs.append(f"{feed}: 429"); time.sleep(3); continue
            d.raise_for_status()
            for p in d.json().get("data") or []:
                a, rel = p.get("attributes") or {}, p.get("relationships") or {}
                if (rel.get("dex") or {}).get("data", {}).get("id") == "pump-fun": continue
                if (num(a.get("reserve_in_usd")) or 0) < C["gt_feed_min_reserve_usd"]: continue
                m = ((rel.get("base_token") or {}).get("data") or {}).get("id", "").split("_", 1)[-1]
                if m and m not in SKIP_BASE: out.setdefault(m, "gt:" + feed.split("?")[0].split("/")[-1])
        except Exception as e: errs.append(f"{feed}: {type(e).__name__}")
        time.sleep(C["sleep_s"])
    return out, errs

def scanner_mints(since_ts):
    """Mints the main scanner logged with a real pool since since_ts (this loop iteration)."""
    out = {}
    try:
        with open("scanner_log.csv", newline="") as f:
            for r in csv.DictReader(f):
                try: t = datetime.fromisoformat(r["time"]).timestamp()
                except Exception: continue
                if t >= since_ts and r.get("liquidity_usd") and r.get("mint"): out.setdefault(r["mint"], "scanner")
    except Exception: pass
    return out

def file_mints():
    """Optional scout_watch.json {"mints": {mint: {"src": "caller:Dug7", "time": iso}}} (callers / influencers)."""
    try: d = json.load(open("scout_watch.json")).get("mints") or {}
    except Exception: return {}
    t = now_ts(); out = {}
    for m, v in d.items():
        try: ts = datetime.fromisoformat(str(v.get("time")).replace("Z", "+00:00")).timestamp()
        except Exception: ts = t
        if t - ts < C["watch_hours"] * 3600: out[m] = str(v.get("src") or "watchfile")[:40]
    return out

_SOL_PX = {}
def sol_price():
    import requests
    if _SOL_PX.get("t", 0) > time.time() - 300: return _SOL_PX["p"]
    d = requests.get(C["jup_price_url"], params={"ids": SOL}, timeout=10).json()
    _SOL_PX.update(p=float(d[SOL]["usdPrice"]), t=time.time()); return _SOL_PX["p"]

def decimals(mint, state):
    import requests
    if mint in state["decimals"]: return state["decimals"][mint]
    d = requests.get("https://lite-api.jup.ag/tokens/v2/search", params={"query": mint}, timeout=10).json()
    for t in d or []:
        if t.get("id") == mint and t.get("decimals") is not None:
            state["decimals"][mint] = int(t["decimals"]); return state["decimals"][mint]
    raise RuntimeError("decimals unknown")

def sell_depth(m, state):
    """Read-only Jupiter QUOTE for selling ~$15 of the token (at the DexScreener price) back to SOL. No wallet, no swap.
    Returns (ok, reason, info). Fails on no route, a quote error, Jupiter price impact > 5%, or getting back more than
    5% less than the pool fee alone would cost (catches fake / unsellable liquidity)."""
    import requests
    usd = C["liq_quote_usd"]; info = {"sell_quote_usd": usd}
    try:
        dec = decimals(m["mint"], state)
        amount = int(usd / m["price_usd"] * 10 ** dec)
        if amount <= 0: return False, "liq:bad_amount", info
        r = requests.get(C["jup_quote_url"], params={"inputMint": m["mint"], "outputMint": SOL, "amount": amount,
                                                       "slippageBps": 300, "restrictIntermediateTokens": "true"}, timeout=15)
        if r.status_code == 429: return False, "liq:quote_rate_limited", info
        d = r.json()
        if r.status_code != 200 or not d.get("outAmount"):
            return False, "liq:no_route" + (f" ({str(d.get('error') or d.get('errorCode') or r.status_code)[:60]})"), info
        back = int(d["outAmount"]) / 1e9 * sol_price(); loss = 100 * (1 - back / usd)
        imp = 100 * float(d.get("priceImpactPct") or 0)
        info.update(sell_back_usd=round(back, 4), sell_loss_pct=round(loss, 2), jup_impact_pct=round(imp, 3))
        allowed = C["max_sell_loss_pct"] + pool_pct(m.get("dex"))
        if imp > C["max_jup_impact_pct"]: return False, f"liq:impact {imp:.1f}%", info
        if loss > allowed: return False, f"liq:sell_loss {loss:.1f}% (> {allowed:.2f}%)", info
        return True, "pass", info
    except Exception as e:
        return False, f"liq:check_error {type(e).__name__}: {str(e)[:60]}", info

def safety(m):
    import second_look
    return second_look.safety(m["mint"], m.get("pair"), m.get("dex"))

# ---------------- forward pass ----------------
def open_trades(src, m, entry, setup, note, t):
    path = C["trade_files"][src]; rows = load_csv(path); n = len(rows)
    for name, spec in EXITS[src].items():
        pos = new_pos(entry, t, spec, setup.get("stop") if setup else None)
        r = {"id": str(n + 1), "opened_at": iso(t), "source": src, "strategy": name, "mint": m["mint"], "symbol": m["symbol"],
             "pair": m["pair"], "dex": m.get("dex") or "", "pool_pct": pool_pct(m.get("dex")), "entry_price": repr(entry),
             "entry_usd": C["ticket_usd"], "stop_price": repr(pos["stop"]), "deadline": str(pos["deadline"]),
             "last_update": iso(t), "price_source": "dexscreener_entry", "entry_note": note[:300]}
        pos_to_row(pos, r, src); rows.append(r); n += 1
    save_csv(rows, path, TRADE_FIELDS)

def update_open(stop_at):
    import candles
    events = []; t = now_ts()
    books = {src: load_csv(p) for src, p in C["trade_files"].items()}
    need = {}
    for src, rows in books.items():
        for r in rows:
            if r["status"] != "open": continue
            k = (r["pair"], r["mint"]); since = int(r["last_candle_ts"])
            prev = need.get(k, (10 ** 12, "9"))
            need[k] = (min(prev[0], since), min(prev[1], r["last_update"]))
    order = sorted(need, key=lambda k: need[k][1])[:C["max_update_coins_per_scan"]]
    cache = {}
    for k in order:
        if time.time() > stop_at: break
        try: cache[k] = candles.ohlcv(k[0], k[1], need[k][0] + 1, max_pages=5)
        except Exception as e: events.append(f"candles unavailable {k[1][:8]} ({type(e).__name__})")
        time.sleep(C["sleep_s"])
    for src, rows in books.items():
        changed = False
        for r in rows:
            k = (r["pair"], r["mint"])
            if r["status"] != "open" or k not in cache: continue
            spec = EXITS[src][r["strategy"]]; pos = row_to_pos(r); pos["tps"] = spec[0]
            cs = [c for c in cache[k] if c[0] > pos["last_ts"]]
            step(pos, cs)
            if pos["status"] == "open" and t >= pos["deadline"]: _close(pos, pos["last"] / pos["entry"], t, "time_exit")
            r["last_update"] = iso(t); r["price_source"] = "candles_1m"; pos_to_row(pos, r, src); changed = True
            if pos["status"] == "closed": events.append(f"[{src}/{r['strategy']}] {r['symbol']} closed {r['exit_reason']} {float(r['pnl_after_fee_usd']):+.2f} after fees")
        if changed: save_csv(rows, C["trade_files"][src], TRADE_FIELDS)
    return events

def check_row(src, m, stage, result, reason, extra=None, note=""):
    r = {"time": iso(), "source": src, **{k: m.get(k) for k in ("mint", "symbol", "pair", "dex", "price_usd", "mcap_usd",
         "liquidity_usd", "vol24_usd", "pool_age_h")}, "stage": stage, "result": result, "reason": reason, "note": note[:200]}
    r.update(extra or {}); append_check(r); return r

def try_buy(src, m, state, setup=None, note=""):
    """Safety + liquidity, then open the paper trades. Returns True if opened."""
    sfx, kill, n2 = safety(m); time.sleep(C["sleep_s"])
    if kill:
        check_row(src, m, "safety", "fail", kill, {"safety_source": sfx}, n2 or note); return False
    ok, why, info = sell_depth(m, state)
    if not ok:
        check_row(src, m, "liquidity", "fail", why, {"safety_source": sfx, **info}, note); return False
    t = now_ts()
    open_trades(src, m, m["price_usd"], setup, f"{note} | safety {sfx} | sell ${info.get('sell_quote_usd')} -> ${info.get('sell_back_usd')} "
                f"({info.get('sell_loss_pct')}% loss, impact {info.get('jup_impact_pct')}%)", t)
    check_row(src, m, "opened", "pass", "paper_buy", {"safety_source": sfx, **info}, note)
    state["bought"][src][m["mint"]] = t
    return True

def run():
    t0 = time.time(); stop_at = t0 + C["max_seconds_per_scan"]; t = now_ts()
    state = load_state(); stats = {"watch": 0, "a_qualify": 0, "a_open": 0, "b_eval": 0, "b_signal": 0, "b_open": 0, "fail": {}}
    for e in update_open(t0 + 45): say("paper: " + e)
    # 1) candidates
    new, errs = gt_feed_mints()
    for e in errs: say("feed error " + e)
    new.update({k: v for k, v in scanner_mints(t - 900).items() if k not in new})
    new.update({k: v for k, v in file_mints().items()})
    for mint, src in new.items():
        if mint not in state["watch"]: state["watch"][mint] = {"t": t, "src": src}
    save_state(state)
    import dexscreener as dx
    try: pairs = dx.pairs_for(list(state["watch"]), 0.4)
    except Exception as e: say(f"dexscreener error {e}"); save_state(state); return
    snaps = {mint: snapshot(mint, p) for mint, p in pairs.items()}
    stats["watch"] = len(snaps)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d"); D = state["days"].setdefault(day, {"pool": 0, "wait3": 0, "pool_slots": []})
    slot = f"{datetime.now(timezone.utc).hour // C['a_slot_hours']}"
    # 2) Strategy A: <= 1 buy per 2h slot, <= 10/day; pick the qualifying coin with the highest 24h volume
    qa = []
    for mint, m in snaps.items():
        if qualifies_a(m): continue
        if t - state["bought"]["pool"].get(mint, 0) < C["a_rebuy_days"] * 86400: continue
        if state["cool"].get("pool|" + mint, 0) > t: continue
        qa.append(m)
    stats["a_qualify"] = len(qa)
    if qa and D["pool"] < C["a_max_per_day"] and slot not in D["pool_slots"]:
        for m in sorted(qa, key=lambda x: -(x["vol24_usd"] or 0))[:C["a_max_attempts_per_scan"]]:
            if time.time() > stop_at: break
            note = f"A: vol24 ${m['vol24_usd']:,.0f} mcap ${m['mcap_usd']:,.0f} liq ${m['liquidity_usd']:,.0f} pool age {m['pool_age_h']}h; watch src {state['watch'][m['mint']]['src']}"
            if try_buy("pool", m, state, None, note):
                D["pool"] += 1; D["pool_slots"].append(slot); stats["a_open"] += 1; break
            state["cool"]["pool|" + m["mint"]] = t + C["a_fail_cooldown_h"] * 3600
    # 3) Strategy B: WAIT-3 on 5-min candles, round-robin over the watchlist
    import candles
    cand = [m for mint, m in snaps.items() if not prefilter_b(m) and mint not in state["bought"]["wait3"]
            and t - state["evals"].get(mint, 0) >= C["b_eval_every_min"] * 60]
    cand.sort(key=lambda m: state["evals"].get(m["mint"], 0))
    for m in cand[:C["b_evals_per_scan"]]:
        if time.time() > stop_at: break
        state["evals"][m["mint"]] = t; stats["b_eval"] += 1
        try:
            since = (m["created_ts"] or t - 72 * 3600) - 300
            cs = candles.ohlcv(m["pair"], m["mint"], since, timeframe="minute", aggregate=5, max_pages=1)
        except Exception as e: say(f"wait3 candles error {m['mint'][:8]} {type(e).__name__}"); time.sleep(C["sleep_s"]); continue
        time.sleep(C["sleep_s"])
        if cs and m["created_ts"] and cs[0][0] > m["created_ts"] + 1800: continue      # pool start not covered: launch unknown
        sigs = list(wait3_signals(cs, m["created_ts"]))
        if not sigs: continue
        s = sigs[-1]
        if t - s["ts"] > C["b_signal_max_age_min"] * 60 or state["w3_seen"].get(m["mint"]) == s["ts"]: continue
        state["w3_seen"][m["mint"]] = s["ts"]; stats["b_signal"] += 1
        note = (f"B: high {s['high']:.4g} low {s['stop']:.4g} higher low {s['higher_low']:.4g} signal close {s['entry']:.4g} "
                f"(launch ref {s['ref']:.4g}); vol 1h ${s['vol_1h']:,} vs ${s['vol_1h_before_high']:,} before high; src {state['watch'][m['mint']]['src']}")
        if m["price_usd"] > s["entry"] * (1 + C["b_max_chase_pct"] / 100) or m["price_usd"] <= s["stop"]:
            check_row("wait3", m, "signal", "fail", "price_moved_away" if m["price_usd"] > s["entry"] else "below_setup_low", note=note); continue
        if D["wait3"] >= C["b_max_per_day"]:
            check_row("wait3", m, "cap", "fail", "daily_cap", note=note); continue
        if try_buy("wait3", m, state, s, note): D["wait3"] += 1; stats["b_open"] += 1
    save_state(state)
    say("pass end " + json.dumps(stats) + f" elapsed={time.time() - t0:.0f}s")

if __name__ == "__main__":
    try: run()
    except Exception:
        say("ERROR " + traceback.format_exc().replace("\n", " | ")); sys.exit(1)
