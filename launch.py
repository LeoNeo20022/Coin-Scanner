# LAUNCH (paper / log only): do coins still on the pump.fun BONDING CURVE (pre-graduation) work with quick exits?
# 1) scanner.py writes the bonding-curve coins it saw this scan to launch_inbox.json (the scanner itself rejects them).
# 2) `python launch.py` (run by loop.sh after each scan) re-fetches them on DexScreener, applies a pre-graduation
#    market filter, then requires a RugCheck report (mint/freeze authority off, no danger risks, no transfer fee,
#    holder / creator / insider concentration caps excluding the curve account, curve progress range, min holders).
#    pump.fun's public coin endpoint is used when reachable (graduated / banned / ATH), but never required.
#    NO Grok calls. Capped sample: max 24 buys/UTC day, 3/hour, 1 per scan.
# 3) A passing coin gets ONE hypothetical $10 paper buy at the DexScreener price at detection, scored under
#    scalp / target5 / hold / flash on GeckoTerminal 1-min candles of the curve pool (and the PumpSwap pool after graduation),
#    each strategy in its own CSV with its own pretend $100. Snapshot-price fallback is labelled price_source=snapshot.
#    flash = +10% / -7% / 15 min (added Oct 22, same entries; older buys backfilled by launch_flash_backfill.py).
#    1-min candles can't see sub-minute moves, so real fills on fast exits would differ.
#    No fees or slippage simulated. Nothing here can trade: read-only public APIs + CSV/JSON files.
import csv, json, os, sys, time, traceback
from datetime import datetime, timezone
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import requests
from config import SCANNER as S, LAUNCH as L
import paper, candles, dexscreener as dx

CHECK_FIELDS = ["time", "mint", "symbol", "pair", "age_min", "price_usd", "mcap_usd", "volume_h1_usd", "buys_h1", "sells_h1",
                "price_change_m5_pct", "price_change_h1_pct", "curve_progress_pct", "curve_sol", "holders", "risk_score",
                "top_holder_pct", "top10_pct", "creator_pct", "insider_network_pct", "ath_ratio", "pumpfun_api", "curve_quote",
                "result", "fail_reason", "hard_fail", "est_buy_impact_pct", "paper_opened", "sources"]
PAPER_FIELDS = paper.FIELDS + ["strategy", "last_candle_ts", "price_source", "dex", "graduated_at", "curve_progress_pct",
                               "est_buy_impact_pct", "curve_quote"]
PF = "https://frontend-api-v3.pump.fun/coins-v2/"
RC = "https://api.rugcheck.xyz/v1/tokens"
UA = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (coin-scanner paper log)"}

def now(): return datetime.now(timezone.utc)
def iso(t=None): return (t or now()).isoformat(timespec="seconds")
def say(msg): print(f"[{iso()}] launch: {msg}", flush=True)
def _num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

# ---------------- state ----------------
def load_state():
    try: return json.load(open(L["state_file"]))
    except Exception: return {"mints": {}, "days": {}}
def save_state(st):
    cut = time.time() - L["state_keep_days"] * 86400
    st["mints"] = {k: v for k, v in st["mints"].items() if v.get("last", 0) > cut}
    st["days"] = dict(sorted(st.get("days", {}).items())[-10:])
    tmp = L["state_file"] + ".tmp"; json.dump(st, open(tmp, "w"), indent=1); os.replace(tmp, L["state_file"])

# ---------------- checks ----------------
def market(mint, p):
    tx = p.get("txns") or {}; vol = p.get("volume") or {}; pc = p.get("priceChange") or {}
    created = p.get("pairCreatedAt")
    return {"mint": mint, "symbol": ((p.get("baseToken") or {}).get("symbol") or "").strip(), "dex": p.get("dexId"),
            "pair": p.get("pairAddress"), "price_usd": _num(p.get("priceUsd")),
            "mcap_usd": p.get("marketCap") if p.get("marketCap") is not None else p.get("fdv"),
            "liquidity_usd": (p.get("liquidity") or {}).get("usd"),
            "age_min": round((time.time() * 1000 - created) / 6e4, 1) if created else None,
            "volume_h1_usd": vol.get("h1"), "buys_h1": (tx.get("h1") or {}).get("buys"), "sells_h1": (tx.get("h1") or {}).get("sells"),
            "price_change_m5_pct": pc.get("m5"), "price_change_h1_pct": pc.get("h1")}

def market_kill(m):
    """Returns (reason, hard). hard = never re-evaluate this coin."""
    if m["dex"] != "pumpfun" or m["liquidity_usd"] is not None: return "graduated_or_not_curve", True
    for k in ("price_usd", "mcap_usd", "age_min", "volume_h1_usd", "buys_h1", "sells_h1"):
        if m.get(k) is None: return f"missing:{k}", False
    if m["age_min"] > L["max_age_hours"] * 60: return "too_old", True
    if m["age_min"] < L["min_age_min"]: return "too_new", False
    if m["mcap_usd"] < L["min_mcap_usd"]: return "mcap_low", False
    if m["volume_h1_usd"] < L["min_volume_h1_usd"]: return "volume_h1_low", False
    if m["buys_h1"] < L["min_buys_h1"]: return "buys_h1_low", False
    if m["buys_h1"] < L["min_buy_sell_ratio_h1"] * m["sells_h1"]: return "sells_gt_buys_h1", False
    if m["price_change_m5_pct"] is not None and m["price_change_m5_pct"] < L["min_price_change_m5_pct"]: return "dumping_m5", False
    return None, False

def rugcheck(mint, pair):
    r = requests.get(f"{RC}/{mint}/report", timeout=20); r.raise_for_status(); d = r.json()
    tok = d.get("token") or {}; supply = _num(tok.get("supply")) or 0
    curve_accts = {pair}
    mk = next((x for x in d.get("markets") or [] if x.get("marketType") == "pump_fun"), None)
    lp = (mk or {}).get("lp") or {}
    if mk:
        for k in ("pubkey", "liquidityAAccount", "liquidityBAccount", "liquidityA", "liquidityB"):
            v = mk.get(k); v = v.get("pubkey") or v.get("owner") if isinstance(v, dict) else v
            if isinstance(v, str): curve_accts.add(v)
    holders = [h for h in d.get("topHolders") or [] if h.get("owner") not in curve_accts and h.get("address") not in curve_accts]
    nets = d.get("insiderNetworks") or []
    net_hold = sum(_num(n.get("currentHolding")) or 0 for n in nets)
    real_tokens = _num(lp.get("base"))
    return {"risk_score": d.get("score_normalised"), "danger": [x.get("name") for x in d.get("risks") or [] if x.get("level") == "danger"],
            "mint_authority": tok.get("mintAuthority"), "freeze_authority": tok.get("freezeAuthority"),
            "transfer_fee_pct": _num((d.get("transferFee") or {}).get("pct")) or 0, "rugged": bool(d.get("rugged")),
            "holders": d.get("totalHolders"),
            "top_holder_pct": round(max([h.get("pct") or 0 for h in holders] or [0]), 2),
            "top10_pct": round(sum(h.get("pct") or 0 for h in holders[:10]), 2),
            "creator_pct": round(100 * (_num(d.get("creatorBalance")) or 0) / supply, 2) if supply else None,
            "insider_network_pct": round(100 * net_hold / supply, 2) if supply else None,
            "curve_progress_pct": round(100 * (1 - real_tokens / L["curve_initial_real_tokens"]), 1) if real_tokens is not None else None,
            "curve_sol": _num(lp.get("quote")), "sol_usd": _num(lp.get("quotePrice")), "curve_found": mk is not None,
            "quote_is_sol": lp.get("quoteMint") == "So11111111111111111111111111111111111111112"}

def token_kill(t):
    if t["risk_score"] is None: return "no_report", False
    if t["mint_authority"] or t["freeze_authority"]: return "authority_open", True
    if t["transfer_fee_pct"] > 0: return "transfer_fee", True
    if t["rugged"]: return "rugged", True
    if t["danger"]: return "danger:" + str(t["danger"][0]), True
    if not t["curve_found"]: return "no_curve_market", False
    if t["risk_score"] > L["max_risk_score"]: return "risk_score", False
    if (t["holders"] or 0) < L["min_holders"]: return "holders_low", False
    if t["curve_progress_pct"] is None or t["curve_progress_pct"] < L["min_curve_progress_pct"]: return "curve_progress_low", False
    if t["curve_progress_pct"] > L["max_curve_progress_pct"]: return "curve_almost_done", False
    if t["top_holder_pct"] > L["max_top_holder_pct"]: return "top_holder", False
    if t["top10_pct"] > L["max_top10_pct"]: return "top10", False
    if t["creator_pct"] is None or t["creator_pct"] > L["max_creator_pct"]: return "creator_holds", False
    if (t["insider_network_pct"] or 0) > L["max_insider_network_pct"]: return "insiders", False
    return None, False

def pumpfun(mint):
    """Optional: pump.fun's public coin endpoint (no auth). Returns dict or None if unreachable."""
    try:
        r = requests.get(PF + mint, headers=UA, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception: return None

def impact_pct(t):
    """Rough average-fill price impact of a $10 buy on the constant-product curve (virtual SOL = real + 30)."""
    if not t.get("quote_is_sol") or not t.get("curve_sol") or not t.get("sol_usd"): return None
    x = t["curve_sol"] + L["curve_virtual_sol_offset"]; dx_ = float(S["paper_ticket_usd"]) / t["sol_usd"]
    return round(100 * dx_ / x, 3)

def append_check(row):
    new = not os.path.exists(L["check_file"])
    with open(L["check_file"], "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CHECK_FIELDS)
        if new: w.writeheader()
        w.writerow({k: row.get(k, "") for k in CHECK_FIELDS})

# ---------------- paper ----------------
def _load(path):
    if not os.path.exists(path): return []
    with open(path, newline="") as f: return list(csv.DictReader(f))
def _save(rows, path):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PAPER_FIELDS); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in PAPER_FIELDS})
    os.replace(tmp, path)

def open_paper(m, t, why):
    opened = []; ts = now(); price = m["price_usd"]; usd = float(S["paper_ticket_usd"])
    for name, path in L["paper_files"].items():
        rows = _load(path)
        if any(r["mint"] == m["mint"] for r in rows): continue      # one hypothetical buy per coin per strategy
        tokens = usd / price
        r = {"id": str(len(rows) + 1), "opened_at": iso(ts), "mint": m["mint"], "symbol": m["symbol"], "pair": m["pair"],
             "entry_price": repr(price), "entry_usd": usd, "tokens_initial": repr(tokens), "tokens_left": repr(tokens),
             "half_taken": "0", "status": "open", "realized_usd": 0, "last_price": repr(price), "last_update": iso(ts),
             "closed_at": "", "exit_reason": "", "events": f"{iso(ts)} BUY ${usd} @ {price} (launch, bonding curve)",
             "confidence": "", "why": why, "strategy": name, "last_candle_ts": str(int(ts.timestamp())), "price_source": "",
             "dex": "pumpfun", "graduated_at": "", "curve_progress_pct": t.get("curve_progress_pct"),
             "est_buy_impact_pct": impact_pct(t), "curve_quote": "SOL" if t.get("quote_is_sol") else "non-SOL"}
        paper._recalc(r); rows.append(r); _save(rows, path); opened.append(name)
    return opened

def _sell(r, tokens, price, label, when):
    proceeds = tokens * price
    r["realized_usd"] = round(float(r["realized_usd"] or 0) + proceeds, 6)
    r["tokens_left"] = repr(max(0.0, float(r["tokens_left"]) - tokens))
    r["events"] += f" | {iso(when)} {label} {tokens:.6g} @ {price:.6g} = ${proceeds:.4f}"
def _close(r, reason, when):
    r["tokens_left"] = "0"; r["status"] = "closed"; r["exit_reason"] = reason; r["closed_at"] = iso(when)

def walk(r, cs, tp_pct, tp_frac, sl_pct, deadline):
    """Same exit rules as second_look.walk / shadow.py: SL first if both in one candle."""
    entry = float(r["entry_price"])
    if tp_frac >= 1:
        hit = candles.first_exit(cs, entry, tp_pct, sl_pct, deadline)
        if hit:
            reason, px, ts = hit; when = datetime.fromtimestamp(ts, timezone.utc)
            _sell(r, float(r["tokens_left"]), px, f"CLOSE_{reason}", when); _close(r, reason, when)
        return
    tp, sl = entry * (1 + tp_pct / 100), entry * (1 + sl_pct / 100)
    for ts, o, h, l, c, _v in cs:
        when = datetime.fromtimestamp(ts, timezone.utc)
        if ts >= deadline: _sell(r, float(r["tokens_left"]), c, "CLOSE_time_exit", when); _close(r, "time_exit", when); return
        if l <= sl: _sell(r, float(r["tokens_left"]), min(sl, o), "CLOSE_stop_loss", when); _close(r, "stop_loss", when); return
        if r["half_taken"] == "0" and h >= tp:
            _sell(r, float(r["tokens_left"]) * tp_frac, max(tp, o), f"TP_HALF(+{tp_pct}%)", when); r["half_taken"] = "1"

def update_paper(sleep_s, max_coins, stop_at):
    events = []; t = now(); now_ts = int(t.timestamp())
    books = {name: _load(path) for name, path in L["paper_files"].items()}
    need = {}   # mint -> (earliest candle ts needed, oldest last_update, pair)
    for rows in books.values():
        for r in rows:
            if r["status"] != "open": continue
            o = int(datetime.fromisoformat(r["opened_at"]).timestamp()); since = max(int(r.get("last_candle_ts") or o), o)
            prev = need.get(r["mint"])
            need[r["mint"]] = (min(prev[0], since), min(prev[1], r["last_update"]), r["pair"]) if prev else (since, r["last_update"], r["pair"])
    if not need: return events
    order = sorted(need, key=lambda k: need[k][1])[:max_coins]
    try: snap = dx.all_pairs_for(order, sleep_s)         # one batched call: graduation check + snapshot fallback
    except Exception as e: snap = {}; events.append(f"snapshot error {type(e).__name__}")
    newpool, cache, failed = {}, {}, set()
    for mint in order:
        if time.time() > stop_at: break
        since, _lu, pair = need[mint]
        ps = snap.get(mint) or []
        grad = [p for p in ps if p.get("dexId") != "pumpfun" and ((p.get("liquidity") or {}).get("usd") or 0) > 0]
        cur = next((p for p in ps if p.get("pairAddress") == pair), None)
        if grad and (cur is None or cur.get("dexId") == "pumpfun"):
            best = max(grad, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
            newpool[mint] = (best["pairAddress"], best.get("dexId"))
        try:
            try: cs = candles.ohlcv(pair, mint, since); time.sleep(sleep_s)
            except Exception:
                if mint not in newpool: raise
                cs = []; time.sleep(sleep_s)        # old curve pool gone from the index: use the new pool only
            if mint in newpool:                       # graduated: final curve trades, then the new pool
                cs2 = candles.ohlcv(newpool[mint][0], mint, since); time.sleep(sleep_s)
                last_curve = cs[-1][0] if cs else since - 1
                cs = cs + [c for c in cs2 if c[0] > last_curve]
            cache[mint] = cs
        except Exception as e:
            failed.add(mint); events.append(f"candles unavailable for {mint[:8]} ({type(e).__name__}), using snapshot price")
            time.sleep(sleep_s)
    for name, rows in books.items():
        tp_pct, tp_frac, sl_pct, max_hold = L["strategies"][name]; changed = False
        for r in rows:
            if r["status"] != "open" or (r["mint"] not in cache and r["mint"] not in failed): continue
            changed = True
            o = int(datetime.fromisoformat(r["opened_at"]).timestamp()); deadline = o + int(max_hold * 60)
            if r["mint"] in newpool and not r.get("graduated_at"):
                r["graduated_at"] = iso(t); r["events"] += f" | {iso(t)} graduated -> {newpool[r['mint']][1]} pool {newpool[r['mint']][0]}"
                r["pair"], r["dex"] = newpool[r["mint"]]
            if r["mint"] in cache:
                since = max(int(r.get("last_candle_ts") or o), o)
                cs = [c for c in cache[r["mint"]] if c[0] >= since and c[0] >= o - o % 60 + 60]   # candles starting after entry minute
                walk(r, cs, tp_pct, tp_frac, sl_pct, deadline)
                if cs: r["last_candle_ts"] = str(int(cs[-1][0])); r["last_price"] = repr(cs[-1][4]) if r["status"] == "open" else r["last_price"]
                if r["status"] == "open" and now_ts >= deadline:
                    _sell(r, float(r["tokens_left"]), float(r["last_price"]), "CLOSE_time_exit", t); _close(r, "time_exit", t)
                if r["status"] == "closed": r["last_price"] = repr(float(r["realized_usd"]) / float(r["tokens_initial"]))  # avg exit, display
                r["price_source"] = "candles_1m"
            else:
                ps = snap.get(r["mint"]) or []
                p = next((x for x in ps if x.get("pairAddress") == r["pair"]), None) or (ps[0] if ps else None)
                px = _num((p or {}).get("priceUsd"))
                if px:
                    r["last_price"] = repr(px); chg = 100 * (px / float(r["entry_price"]) - 1); r["price_source"] = "snapshot"
                    if r["half_taken"] == "0" and chg >= tp_pct and tp_frac < 1:
                        _sell(r, float(r["tokens_left"]) * tp_frac, px, "TP_HALF[snapshot]", t); r["half_taken"] = "1"
                    reason = "stop_loss" if chg <= sl_pct else "take_profit" if (chg >= tp_pct and tp_frac >= 1) else \
                             "time_exit" if now_ts >= deadline else None
                    if reason: _sell(r, float(r["tokens_left"]), px, f"CLOSE_{reason}[snapshot]", t); _close(r, reason, t)
            r["last_update"] = iso(t); paper._recalc(r)
            if r["status"] == "closed": events.append(f"[{name}] {r['symbol']}: closed ({r['exit_reason']}) {r['pnl_pct']:+.1f}%")
        if changed: _save(rows, path=L["paper_files"][name])
    return events

def flash_catchup():
    """Same entries for flash: copy recent scalp entries missing from the flash book (e.g. opened by an older code version
    mid-run). Never a new buy decision. Older gaps are left to launch_flash_backfill.py."""
    path = L["paper_files"].get("flash")
    if not path: return 0
    fl = _load(path); have = {r["mint"] for r in fl}; cut = time.time() - L.get("flash_catchup_hours", 3) * 3600; n = 0
    for s in sorted(_load(L["paper_files"]["scalp"]), key=lambda r: r["opened_at"]):
        if s["mint"] in have or datetime.fromisoformat(s["opened_at"]).timestamp() < cut: continue
        price = float(s["entry_price"]); usd = float(s["entry_usd"]); tokens = usd / price
        r = {k: s.get(k, "") for k in PAPER_FIELDS}
        r.update({"id": str(len(fl) + 1), "tokens_initial": repr(tokens), "tokens_left": repr(tokens), "half_taken": "0",
                  "status": "open", "realized_usd": 0, "last_price": repr(price), "last_update": s["opened_at"], "closed_at": "",
                  "exit_reason": "", "strategy": "flash", "price_source": "",
                  "last_candle_ts": str(int(datetime.fromisoformat(s["opened_at"]).timestamp())),
                  "events": f"{s['opened_at']} BUY ${usd} @ {price} (launch, bonding curve; flash catch-up copy of scalp entry)"})
        paper._recalc(r); fl.append(r); have.add(s["mint"]); n += 1
    if n: _save(fl, path)
    return n

# ---------------- one pass ----------------
def run():
    t0 = time.time(); sl = S["sleep_between_calls_s"]; stop_at = t0 + L["max_seconds_per_scan"]
    st = load_state(); nowts = time.time(); tnow = now()
    day, hour = tnow.strftime("%Y-%m-%d"), tnow.strftime("%Y-%m-%dT%H")
    d = st.setdefault("days", {}).setdefault(day, {"buys": 0, "hours": {}, "evals": 0, "fails": {}})
    stats = {"inbox": 0, "eligible": 0, "market_fail": 0, "rugchecked": 0, "token_fail": 0, "opened": 0, "skipped_cap": 0}
    try: inbox = json.load(open(L["inbox_file"]))
    except Exception: inbox = {"mints": {}}
    fresh_inbox = inbox.get("time", "") >= iso(datetime.fromtimestamp(nowts - 600, timezone.utc))   # only this scan's sightings
    cands = []
    for mint, src in (inbox.get("mints") or {}).items() if fresh_inbox else []:
        e = st["mints"].setdefault(mint, {"first": nowts, "evals": 0, "next": 0})
        e["last"] = nowts; e.setdefault("src", src)
        if e.get("done") or nowts < e.get("next", 0) or e["evals"] >= L["max_evals_per_mint"]: continue
        cands.append(mint)
    stats["inbox"] = len(inbox.get("mints") or {}) if fresh_inbox else 0; stats["eligible"] = len(cands)
    if cands:
        try: pairs = dx.all_pairs_for(cands, sl)
        except Exception as ex: say(f"pairs error {ex}"); pairs = {}
        ms = []
        for mint in cands:
            ps = [p for p in pairs.get(mint) or [] if p.get("chainId") == "solana"]
            if not ps: continue
            cur = next((p for p in ps if p.get("dexId") == "pumpfun"), ps[0])
            m = market(mint, cur)
            if any(p.get("dexId") != "pumpfun" for p in ps): m["dex"] = "graduated"
            ms.append(m)
        ms.sort(key=lambda m: -(m["volume_h1_usd"] or 0))     # most active first
        rc_used = 0
        for m in ms:
            e = st["mints"][m["mint"]]; row = {k: m.get(k) for k in CHECK_FIELDS if k in m}; row["time"] = iso(); row["sources"] = e.get("src", "")
            k, hard = market_kill(m)
            if k:
                stats["market_fail"] += 1; d["fails"][k] = d["fails"].get(k, 0) + 1
                e["evals"] += 1 if k not in ("too_new",) else 0; e["next"] = nowts + L["reeval_after_min"] * 60
                if hard: e["done"] = True
                e["why"] = k; continue                     # market fails are counted, not written row by row
            capped = d["buys"] >= L["max_buys_per_day"] or d["hours"].get(hour, 0) >= L["max_buys_per_hour"] \
                     or stats["opened"] >= L["max_buys_per_scan"]
            if capped: stats["skipped_cap"] += 1; continue          # not counted as an eval; may be picked next scan
            if rc_used >= L["max_rugchecks_per_scan"] or time.time() > stop_at: break
            rc_used += 1; stats["rugchecked"] += 1; e["evals"] += 1; d["evals"] += 1
            try: t = rugcheck(m["mint"], m["pair"])
            except Exception as ex:
                t = None; row.update({"result": "fail", "fail_reason": f"rugcheck_error {type(ex).__name__}", "hard_fail": "0"})
            time.sleep(sl)
            if t is not None:
                k, hard = token_kill(t)
                row.update({x: t.get(x) for x in ("curve_progress_pct", "curve_sol", "holders", "risk_score", "top_holder_pct",
                                                   "top10_pct", "creator_pct", "insider_network_pct")})
                pf = pumpfun(m["mint"]) if not k else None; row["pumpfun_api"] = ("ok" if pf else "unavailable") if not k else ""
                if pf and pf.get("quote_mint") not in (None, "", "11111111111111111111111111111111", "So11111111111111111111111111111111111111112"):
                    t["quote_is_sol"] = False                  # e.g. a PUMP-quoted curve: RugCheck's SOL figures don't apply
                row["curve_quote"] = "SOL" if t.get("quote_is_sol") else ((pf or {}).get("quote_mint") or "?")[:8]
                row["est_buy_impact_pct"] = impact_pct(t)
                if not k:
                    if pf:
                        ath = _num(pf.get("ath_market_cap")); mc = _num(pf.get("usd_market_cap"))
                        # ath_market_cap looks USD-denominated (market_cap is in SOL); ignore if units look off (>1.5)
                        if ath and mc and mc / ath <= 1.5: row["ath_ratio"] = round(mc / ath, 2)
                        if pf.get("complete"): k, hard = "graduated", True
                        elif pf.get("is_banned"): k, hard = "pumpfun_banned", True
                        elif row.get("ath_ratio") not in (None, "") and row["ath_ratio"] < L["min_ath_ratio"]: k, hard = "far_below_ath", False
                row.update({"result": "fail" if k else "pass", "fail_reason": k or "", "hard_fail": "1" if hard else "0"})
            if row["result"] == "fail":
                stats["token_fail"] += 1; fr = row["fail_reason"].split(" ")[0]; d["fails"][fr] = d["fails"].get(fr, 0) + 1
                e["next"] = nowts + L["reeval_after_min"] * 60; e["why"] = row["fail_reason"]
                if row["hard_fail"] == "1": e["done"] = True
                row["paper_opened"] = "0"; append_check(row)
                say(f"{m['symbol']} {m['mint'][:8]}… fail: {row['fail_reason']}"); continue
            why = (f"launch: curve {t['curve_progress_pct']}%, holders {t['holders']}, top {t['top_holder_pct']}%, "
                   f"top10 {t['top10_pct']}%, vol1h ${m['volume_h1_usd']:.0f}, age {m['age_min']:.0f}m")
            opened = open_paper(m, t, why); e["done"] = True; e["why"] = "bought"
            row["paper_opened"] = "1" if opened else "0"; append_check(row)
            if opened:
                stats["opened"] += 1; d["buys"] += 1; d["hours"][hour] = d["hours"].get(hour, 0) + 1
                say(f"PAPER BUY ${S['paper_ticket_usd']} x{len(opened)} strategies {m['symbol']} {m['mint'][:8]}… @ {m['price_usd']} ({why})")
    d["hours"] = {h: v for h, v in d["hours"].items() if h.startswith(day)}
    save_state(st)
    try:
        nc = flash_catchup()
        if nc: say(f"flash: copied {nc} recent scalp entries")
    except Exception as ex: say(f"flash catch-up error {type(ex).__name__}")
    for ev in update_paper(sl, L["max_paper_coins_per_scan"], stop_at + 30): say("paper: " + ev)
    say("pass end " + json.dumps(stats) + f" today buys {d['buys']}/{L['max_buys_per_day']} elapsed={time.time() - t0:.0f}s")

if __name__ == "__main__":
    try: run()
    except Exception:
        say("ERROR " + traceback.format_exc().replace("\n", " | ")); sys.exit(1)
