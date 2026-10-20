# SECOND LOOK (paper / log only): do coins the scanner REJECTED look better 24h and 48h later?
# 1) scanner.py calls consider() at each rejection; a sampled, deduped subset goes into second_look_queue.json.
# 2) `python second_look.py` (run by loop.sh after each scan) re-checks due coins at ~24h and ~48h with the SAME
#    free checks (market filter + RugCheck, GoPlus/Jupiter/RPC fallback). NO Grok calls (protects the 40/day budget).
#    Results -> second_look.csv.
# 3) A coin that would pass a recheck gets ONE hypothetical $10 paper buy at the recheck price (first passing
#    checkpoint only), scored under hold / target5 / scalp on GeckoTerminal 1-min candles, each in its own CSV.
#    No fees or slippage simulated. Nothing here can trade: read-only public APIs + CSV/JSON files.
import csv, json, os, sys, time, traceback
from datetime import datetime, timezone
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from config import SCANNER as S, SHADOW, SECOND_LOOK as SL
import paper, candles, dexscreener as dx

CHECK_FIELDS = ["time", "mint", "symbol", "checkpoint", "rejected_at", "reject_stage", "reject_reason", "hours_since_reject",
                "price_at_reject", "mcap_at_reject", "liq_at_reject", "price_usd", "mcap_usd", "liquidity_usd", "volume_h1_usd",
                "volume_h24_usd", "buys_h1", "sells_h1", "age_hours", "price_change_vs_reject_pct", "dead", "dead_reason",
                "market_result", "safety_source", "safety_result", "would_pass", "pass_note", "paper_opened", "pair", "dex"]
PAPER_FIELDS = paper.FIELDS + ["strategy", "last_candle_ts", "price_source", "checkpoint", "reject_stage", "reject_reason"]
STRATS = {   # name -> (file, tp_pct, tp_fraction, sl_pct, max_hold_min)
    "hold":    (SL["paper_files"]["hold"], S["take_profit_pct"], S["take_profit_fraction"], S["stop_loss_pct"], S["max_hold_hours"] * 60),
    "target5": (SL["paper_files"]["target5"], SHADOW["target5"]["tp_pct"], 1.0, SHADOW["target5"]["sl_pct"], SHADOW["target5"]["max_hold_min"]),
    "scalp":   (SL["paper_files"]["scalp"], SHADOW["scalp"]["tp_pct"], 1.0, SHADOW["scalp"]["sl_pct"], SHADOW["scalp"]["max_hold_min"]),
}

def now(): return datetime.now(timezone.utc)
def iso(t=None): return (t or now()).isoformat(timespec="seconds")
def say(msg): print(f"[{iso()}] second_look: {msg}", flush=True)

# ---------------- queue ----------------
def load_queue():
    try: return json.load(open(SL["queue_file"]))
    except Exception: return {"entries": {}, "days": {}}
def save_queue(q):
    cutoff = time.time() - SL["queue_keep_days"] * 86400   # forget finished entries after a while (CSV keeps the history)
    q["entries"] = {k: v for k, v in q["entries"].items() if not v.get("done") or v["rejected_ts"] > cutoff}
    q["days"] = {d: v for d, v in sorted(q.get("days", {}).items())[-10:]}
    tmp = SL["queue_file"] + ".tmp"; json.dump(q, open(tmp, "w"), indent=1); os.replace(tmp, SL["queue_file"])

def reason_key(stage, reason):
    """Bucket used for variety caps: 'danger:Foo' -> 'danger', 'fb:top10' -> 'top10'."""
    if stage == "grok": return "grok:skip"
    r = (reason or "").split(" ")[0]
    r = r[3:] if r.startswith("fb:") else r
    return f"{stage}:{r.split(':')[0]}"

class Sampler:
    """Collects rejections during one scan; commit() writes the sampled ones to the queue file once."""
    def __init__(self):
        self.q = load_queue(); self.added = []
    def consider(self, m, stage, reason, src=""):
        try:
            mint = m.get("mint"); r = (reason or "").split(" ")[0]
            if not mint or mint in self.q["entries"]: return False          # dedupe by mint, ever
            if any(r == x or r.startswith(x) for x in SL["skip_reasons"]): return False
            if m.get("price_usd") is None and r != "pumpfun_bonding_curve": return False   # no price -> nothing to compare
            t = now(); day, hour = t.strftime("%Y-%m-%d"), t.strftime("%H")
            d = self.q.setdefault("days", {}).setdefault(day, {"total": 0, "hours": {}, "reasons": {}})
            k = reason_key(stage, reason)
            if d["total"] >= SL["max_new_per_day"]: return False
            if d["reasons"].get(k, 0) >= SL["max_per_reason_per_day"]: return False
            hk = f"{hour}|{k}"
            if stage == "market" and (d["hours"].get(hour, 0) >= SL["max_market_per_hour"] or d["hours"].get(hk)):
                return False          # spread over the day: <=3 market rejects per hour, <=1 per reason per hour
            d["total"] += 1; d["reasons"][k] = d["reasons"].get(k, 0) + 1
            if stage == "market": d["hours"][hour] = d["hours"].get(hour, 0) + 1; d["hours"][hk] = 1
            self.q["entries"][mint] = {
                "mint": mint, "symbol": m.get("symbol") or "", "pair": m.get("pair") or "", "dex": m.get("dex") or "",
                "rejected_at": iso(t), "rejected_ts": int(t.timestamp()), "stage": stage, "reason": (reason or "")[:160],
                "reason_key": k, "sources": src, "price_usd": m.get("price_usd"), "mcap_usd": m.get("mcap_usd"),
                "liquidity_usd": m.get("liquidity_usd"), "age_hours": m.get("age_hours"), "checks": {}, "done": False}
            self.added.append(mint); return True
        except Exception: return False      # never let sampling break the scan
    def commit(self):
        if self.added: save_queue(self.q)
        return len(self.added)

# ---------------- recheck ----------------
def safety(mint, pair, dex):
    """Same free token checks as the scanner (RugCheck, else fallback). Returns (source, kill_or_None, note)."""
    from rugcheck import report, fact_kill
    import fallback
    try:
        t = report(mint); return "rugcheck", fact_kill(t), ""
    except Exception as e:
        rc_err = f"{type(e).__name__}: {str(e)[:60]}"
    try:
        t = fallback.report(mint, pair, dex); k = fallback.fact_kill(t)
        return t["source"], ("fb:" + k) if k else None, f"rugcheck_error {rc_err}"
    except Exception as e2:
        return "none", "check_error", f"rugcheck_error {rc_err} | fallback_error {str(e2)[:80]}"

def _num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def recheck(e, cp, p, sleep_s):
    import scanner   # reuse the scanner's own market parsing and market filter (no side effects on import)
    t = now(); row = {k: "" for k in CHECK_FIELDS}
    row.update({"time": iso(t), "mint": e["mint"], "symbol": e["symbol"], "checkpoint": cp, "rejected_at": e["rejected_at"],
                "reject_stage": e["stage"], "reject_reason": e["reason"],
                "hours_since_reject": round((t.timestamp() - e["rejected_ts"]) / 3600, 1),
                "price_at_reject": e.get("price_usd"), "mcap_at_reject": e.get("mcap_usd"), "liq_at_reject": e.get("liquidity_usd"),
                "would_pass": "0", "paper_opened": "0"})
    if not p:
        row.update({"dead": "1", "dead_reason": "no_pair", "market_result": "no_solana_pair"}); return row, None
    m = scanner.market(e["mint"], p)
    row.update({k: m.get(k) for k in ("symbol", "price_usd", "mcap_usd", "liquidity_usd", "volume_h1_usd", "buys_h1", "sells_h1",
                                      "age_hours", "pair", "dex")})
    row["symbol"] = m.get("symbol") or e["symbol"]
    row["volume_h24_usd"] = (p.get("volume") or {}).get("h24")
    p0, p1 = _num(e.get("price_usd")), m.get("price_usd")
    if p0 and p1: row["price_change_vs_reject_pct"] = round(100 * (p1 / p0 - 1), 1)
    liq, v24 = m.get("liquidity_usd"), _num(row["volume_h24_usd"]) or 0
    curve = m.get("dex") == "pumpfun" and liq is None          # still on the pump.fun bonding curve: no pool liquidity yet
    dead = "no_price" if p1 is None else "liquidity_gone" if not curve and (liq or 0) < SL["dead_liquidity_usd"] else \
           "no_volume_24h" if v24 < SL["dead_volume_h24_usd"] else \
           "price_-90%" if row["price_change_vs_reject_pct"] != "" and row["price_change_vs_reject_pct"] <= -90 else ""
    row["dead"], row["dead_reason"] = ("1", dead) if dead else ("0", "")
    k = scanner.market_kill(m); note = []
    if k == "too_old" and SL["waive_max_age"]:   # the coin aged during the wait; judge everything else
        note.append("age>72h waived"); k = None
    row["market_result"] = k or "pass"
    if k: return row, None
    src, kill, n2 = safety(e["mint"], m.get("pair"), m.get("dex")); time.sleep(sleep_s)
    row["safety_source"], row["safety_result"] = src, kill or "pass"
    if n2: note.append(n2)
    row["pass_note"] = "; ".join(note)
    if kill: return row, None
    row["would_pass"] = "1"
    return row, m

def append_check(row):
    new = not os.path.exists(SL["check_file"])
    with open(SL["check_file"], "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CHECK_FIELDS)
        if new: w.writeheader()
        w.writerow(row)

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

def open_paper(e, cp, m):
    opened = []; t = now(); price = m["price_usd"]; usd = float(S["paper_ticket_usd"])
    for name, (path, *_rest) in STRATS.items():
        rows = _load(path)
        if any(r["mint"] == e["mint"] for r in rows): continue      # one hypothetical buy per coin per strategy
        tokens = usd / price
        r = {"id": str(len(rows) + 1), "opened_at": iso(t), "mint": e["mint"], "symbol": m.get("symbol") or e["symbol"],
             "pair": m.get("pair"), "entry_price": repr(price), "entry_usd": usd, "tokens_initial": repr(tokens),
             "tokens_left": repr(tokens), "half_taken": "0", "status": "open", "realized_usd": 0, "last_price": repr(price),
             "last_update": iso(t), "closed_at": "", "exit_reason": "", "events": f"{iso(t)} BUY ${usd} @ {price} (second look {cp})",
             "confidence": "", "why": f"second look {cp}: rejected {e['rejected_at'][:16]} for {e['reason'][:80]}",
             "strategy": name, "last_candle_ts": str(int(t.timestamp())), "price_source": "",
             "checkpoint": cp, "reject_stage": e["stage"], "reject_reason": e["reason"][:160]}
        paper._recalc(r); rows.append(r); _save(rows, path); opened.append(name)
    return opened

def _sell(r, tokens, price, label, when):
    proceeds = tokens * price
    r["realized_usd"] = round(float(r["realized_usd"] or 0) + proceeds, 6)
    r["tokens_left"] = repr(max(0.0, float(r["tokens_left"]) - tokens))
    r["events"] += f" | {iso(when)} {label} {tokens:.6g} @ {price:.6g} = ${proceeds:.4f}"

def walk(r, cs, tp_pct, tp_frac, sl_pct, deadline):
    """Apply exits candle by candle (oldest first). Full-exit strategies use candles.first_exit (same as shadow.py).
    Hold: sell tp_frac at +tp_pct, stop the rest at sl_pct, time exit at deadline. SL first if both in one candle."""
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
        if ts >= deadline:
            _sell(r, float(r["tokens_left"]), c, "CLOSE_time_exit", when); _close(r, "time_exit", when); return
        if l <= sl:
            _sell(r, float(r["tokens_left"]), min(sl, o), "CLOSE_stop_loss", when); _close(r, "stop_loss", when); return
        if r["half_taken"] == "0" and h >= tp:
            _sell(r, float(r["tokens_left"]) * tp_frac, max(tp, o), f"TP_HALF(+{tp_pct}%)", when); r["half_taken"] = "1"

def _close(r, reason, when):
    r["tokens_left"] = "0"; r["status"] = "closed"; r["exit_reason"] = reason; r["closed_at"] = iso(when)

def update_paper(sleep_s, max_coins, stop_at):
    """Mark open second-look paper positions on 1-min candles (one GeckoTerminal fetch per coin per scan, oldest-updated
    coins first, at most max_coins per scan). Snapshot (DexScreener) price fallback if candles fail."""
    events = []; t = now(); now_ts = int(t.timestamp())
    books = {name: _load(spec[0]) for name, spec in STRATS.items()}
    need = {}
    for rows in books.values():
        for r in rows:
            if r["status"] != "open": continue
            k = (r["pair"], r["mint"]); o = int(datetime.fromisoformat(r["opened_at"]).timestamp())
            since = max(int(r.get("last_candle_ts") or o), o)
            prev = need.get(k, (10**12, ""))
            need[k] = (min(prev[0], since), min(prev[1], r["last_update"]) if prev[1] else r["last_update"])
    order = sorted(need, key=lambda k: need[k][1])[:max_coins]
    cache, failed = {}, []
    for k in order:
        if time.time() > stop_at: break
        try: cache[k] = candles.ohlcv(k[0], k[1], need[k][0])
        except Exception as e: failed.append(k); events.append(f"candles unavailable for {k[1][:8]} ({type(e).__name__})")
        time.sleep(sleep_s)
    snap = {}
    if failed:
        try: snap = dx.all_pairs_for([k[1] for k in failed], sleep_s)
        except Exception as e: events.append(f"snapshot error {e}")
    for name, (path, tp_pct, tp_frac, sl_pct, hold_min) in STRATS.items():
        rows = books[name]; changed = False
        for r in rows:
            k = (r["pair"], r["mint"])
            if r["status"] != "open" or (k not in cache and k not in failed): continue
            o = int(datetime.fromisoformat(r["opened_at"]).timestamp()); deadline = o + int(hold_min * 60)
            since = max(int(r.get("last_candle_ts") or o), o); changed = True
            if k in cache:
                cs = [c for c in cache[k] if c[0] >= since and c[0] >= o - o % 60 + 60]   # candles that start after entry
                walk(r, cs, tp_pct, tp_frac, sl_pct, deadline)
                if cs:
                    r["last_candle_ts"] = str(int(cs[-1][0]))
                    if r["status"] == "open": r["last_price"] = repr(cs[-1][4])
                if r["status"] == "open" and now_ts >= deadline:    # no later trade: exit at last known price
                    _sell(r, float(r["tokens_left"]), float(r["last_price"]), "CLOSE_time_exit", t); _close(r, "time_exit", t)
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
            if r["status"] == "closed":
                events.append(f"[{name}] {r['symbol']}: closed ({r['exit_reason']})")
            r["last_update"] = iso(t); paper._recalc(r)
        if changed: _save(rows, path)
    return events

# ---------------- one pass ----------------
def run():
    t0 = time.time(); sl = S["sleep_between_calls_s"]; stop_at = t0 + SL["max_seconds_per_scan"]
    q = load_queue(); nowts = time.time(); stats = {"queued": len(q["entries"]), "due": 0, "rechecked": 0, "pass": 0, "opened": 0}
    due = []
    for e in q["entries"].values():
        if e.get("done"): continue
        for cp, hrs in SL["checkpoints"].items():
            if cp in e["checks"]: continue
            if nowts - e["rejected_ts"] >= hrs * 3600: due.append((e, cp))
            break    # checkpoints in order: 48h only after 24h is done
    stats["due"] = len(due)
    due.sort(key=lambda x: x[0]["rejected_ts"])
    due = due[:SL["max_rechecks_per_scan"]]
    if due:
        try: pairs = dx.pairs_for([e["mint"] for e, _ in due], sl)
        except Exception as ex: say(f"pairs error {ex}; retry next scan"); pairs = None
        if pairs is not None:
            for e, cp in due:
                if time.time() > stop_at: break
                try: row, m = recheck(e, cp, pairs.get(e["mint"]), sl)
                except Exception as ex: say(f"recheck error {e['mint'][:8]} {type(ex).__name__}: {ex}"); continue
                if row["safety_result"] == "check_error": row["pass_note"] = (row["pass_note"] or "") + " (safety sources down)"
                if m is not None:
                    opened = open_paper(e, cp, m)
                    if opened: row["paper_opened"] = "1"; stats["opened"] += 1
                    stats["pass"] += 1
                append_check(row); stats["rechecked"] += 1
                e["checks"][cp] = {"time": row["time"], "would_pass": row["would_pass"], "dead": row["dead"],
                                   "result": row["safety_result"] or row["market_result"]}
                if cp == list(SL["checkpoints"])[-1]: e["done"] = True
                say(f"{cp} {row['symbol']} {e['mint'][:8]}… (rejected {e['reason_key']}) -> "
                    f"{'WOULD PASS' if row['would_pass'] == '1' else 'fail: ' + (row['safety_result'] or row['market_result'])}"
                    f"{' [dead: ' + row['dead_reason'] + ']' if row['dead'] == '1' else ''}"
                    f"{' -> paper buy $' + str(S['paper_ticket_usd']) + ' x3 strategies' if row['paper_opened'] == '1' else ''}")
            save_queue(q)
    for ev in update_paper(sl, SL["max_paper_coins_per_scan"], stop_at + 30): say("paper: " + ev)
    say("pass end " + json.dumps(stats) + f" elapsed={time.time() - t0:.0f}s")

if __name__ == "__main__":
    try: run()
    except Exception:
        say("ERROR " + traceback.format_exc().replace("\n", " | ")); sys.exit(1)
