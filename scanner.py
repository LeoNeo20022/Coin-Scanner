# LOW-CAP SOLANA SCANNER — LOG ONLY (GitHub Actions edition: one pass per invocation).
# Places no trades, touches no wallet, never talks to fomo or any trading bot, signs nothing.
# It reads free public APIs (DexScreener, RugCheck), asks Grok BUY/SKIP, and writes CSV logs + paper trades.
import csv, fcntl, json, os, sys, time, traceback
from datetime import datetime, timezone
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from config import SCANNER as S, MIN_CONFIDENCE
from prompts import SCANNER_PROMPT
from rugcheck import report, fact_kill
import fallback
from grok import ask, NoResponse
import dexscreener as dx, paper

LOG_FIELDS = ["time", "mint", "symbol", "mcap_usd", "liquidity_usd", "age_hours", "volume_h1_usd", "buys_h1",
              "sells_h1", "stage", "verdict", "confidence", "reason", "main_risk", "sources"]
NEEDED_MARKET = ["mcap_usd", "liquidity_usd", "age_hours", "volume_h1_usd", "buys_h1", "sells_h1",
                 "price_change_m5_pct", "price_change_h1_pct", "price_change_h6_pct", "price_usd"]
NEEDED_TOKEN = ["risk_score", "top10_pct", "lp_locked_pct"]
NEEDED_TOKEN_FALLBACK = ["top10_pct", "lp_locked_pct"]   # fallback has no risk score / insider data; see fallback.py

def now(): return datetime.now(timezone.utc)
def iso(t=None): return (t or now()).isoformat(timespec="seconds")
def say(msg): print(f"[{iso()}] {msg}", flush=True)

def load_seen():
    try: return json.load(open(S["seen_file"]))
    except Exception: return {}
def save_seen(seen):
    cutoff = time.time() - 7 * 86400                      # prune entries older than a week
    seen = {k: v for k, v in seen.items() if max(v.get(f, 0) for f in ("judged", "market_logged", "rc_err_logged", "deferred_logged")) > cutoff}
    tmp = S["seen_file"] + ".tmp"; json.dump(seen, open(tmp, "w"), indent=1); os.replace(tmp, S["seen_file"])

def grok_budget_limit():
    try: return int(os.environ.get("GROK_DAILY_BUDGET", S["max_grok_calls_per_day"]))
    except ValueError: return S["max_grok_calls_per_day"]
def load_budget():
    today = now().strftime("%Y-%m-%d")
    try: b = json.load(open(S["grok_budget_file"]))
    except Exception: b = {}
    if b.get("date") != today: b = {"date": today, "calls": 0}   # new UTC day -> reset
    b.setdefault("failed", 0); b.setdefault("cost_usd", 0.0)
    return b
def save_budget(b):
    tmp = S["grok_budget_file"] + ".tmp"; json.dump(b, open(tmp, "w")); os.replace(tmp, S["grok_budget_file"])

def log(m, stage, verdict="SKIP", confidence="", reason="", main_risk="", sources=""):
    row = {"time": iso(), "mint": m.get("mint"), "symbol": m.get("symbol"),
           "mcap_usd": m.get("mcap_usd"), "liquidity_usd": m.get("liquidity_usd"), "age_hours": m.get("age_hours"),
           "volume_h1_usd": m.get("volume_h1_usd"), "buys_h1": m.get("buys_h1"), "sells_h1": m.get("sells_h1"),
           "stage": stage, "verdict": verdict, "confidence": confidence, "reason": reason, "main_risk": main_risk,
           "sources": sources}
    new = not os.path.exists(S["log_file"])
    with open(S["log_file"], "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new: w.writeheader()
        w.writerow(row)

def market(mint, p):
    tx = p.get("txns") or {}; vol = p.get("volume") or {}; pc = p.get("priceChange") or {}
    created = p.get("pairCreatedAt")
    mcap = p.get("marketCap") if p.get("marketCap") is not None else p.get("fdv")
    liq = (p.get("liquidity") or {}).get("usd")
    try: price = float(p["priceUsd"]) if p.get("priceUsd") else None
    except ValueError: price = None
    return {"mint": mint, "symbol": ((p.get("baseToken") or {}).get("symbol") or "").strip(),
            "dex": p.get("dexId"), "pair": p.get("pairAddress"), "price_usd": price,
            "mcap_usd": mcap, "liquidity_usd": liq,
            "age_hours": round((time.time() * 1000 - created) / 3.6e6, 2) if created else None,
            "volume_h1_usd": vol.get("h1"), "volume_h6_usd": vol.get("h6"),
            "buys_h1": (tx.get("h1") or {}).get("buys"), "sells_h1": (tx.get("h1") or {}).get("sells"),
            "buys_m5": (tx.get("m5") or {}).get("buys"), "sells_m5": (tx.get("m5") or {}).get("sells"),
            "price_change_m5_pct": pc.get("m5"), "price_change_h1_pct": pc.get("h1"), "price_change_h6_pct": pc.get("h6"),
            "liquidity_to_mcap": round(liq / mcap, 3) if liq and mcap else None}

def market_kill(m):
    if m.get("dex") == "pumpfun" and m.get("liquidity_usd") is None: return "pumpfun_bonding_curve"   # not graduated, no pool yet
    for k in ("mcap_usd", "liquidity_usd", "age_hours", "volume_h1_usd", "buys_h1", "sells_h1", "price_usd"):
        if m.get(k) is None: return f"missing:{k}"
    if m["mcap_usd"] < S["min_mcap_usd"]: return "mcap_too_low"
    if m["mcap_usd"] > S["max_mcap_usd"]: return "mcap_too_high"
    if m["liquidity_usd"] < S["min_liquidity_usd"]: return "liquidity_low"
    if m["age_hours"] < S["min_age_hours"]: return "too_new"
    if m["age_hours"] > S["max_age_hours"]: return "too_old"
    if m["volume_h1_usd"] < S["min_volume_h1_usd"]: return "volume_h1_low"
    if S["require_buys_gt_sells_h1"] and m["buys_h1"] <= m["sells_h1"]: return "sells_ge_buys_h1"
    return None

def run():
    t0 = time.time(); sl = S["sleep_between_calls_s"]
    stats = {"discovered": 0, "skipped_recent": 0, "no_pair": 0, "market": {}, "rug": {}, "grok": 0,
             "BUY": 0, "SKIP": 0, "deferred": 0, "deferred_budget": 0, "fallback": 0}
    say("run start (LOG ONLY)")
    for e in paper.update_open(sl): say("paper: " + e)

    found, errs = dx.discover(sl)
    for e in errs: say("discovery_error " + e)
    stats["discovered"] = len(found)
    seen = load_seen(); nowts = time.time()
    fresh = [m for m in found if nowts - seen.get(m, {}).get("judged", 0) > S["rejudge_after_hours"] * 3600
             and nowts >= seen.get(m, {}).get("grok_retry_at", 0)]
    stats["skipped_recent"] = len(found) - len(fresh)
    try:
        pairs = dx.pairs_for(fresh, sl) if fresh else {}
    except Exception as e:
        say(f"pairs_error {e}"); pairs = {}

    survivors = []
    for mint in fresh:
        src = ",".join(found[mint]); ent = seen.setdefault(mint, {})
        relog = nowts - ent.get("market_logged", 0) > S["market_relog_hours"] * 3600
        if mint not in pairs:
            stats["no_pair"] += 1
            if relog: log({"mint": mint}, "market", reason="no_solana_pair", sources=src); ent["market_logged"] = nowts
            continue
        m = market(mint, pairs[mint]); k = market_kill(m)
        if k:
            stats["market"][k] = stats["market"].get(k, 0) + 1
            if relog or ent.get("market_reason") != k:
                log(m, "market", reason=k, sources=src); ent["market_logged"] = nowts; ent["market_reason"] = k
            continue
        survivors.append((m, src))
    say(f"discovered={len(found)} fresh={len(fresh)} market_pass={len(survivors)}")

    grok_calls = 0
    budget = load_budget(); limit = grok_budget_limit()
    say(f"grok daily budget {budget['calls']}/{limit} used, {budget['failed']} no-response, ${budget['cost_usd']:.4f} ({budget['date']} UTC)")
    for m, src in survivors:
        mint = m["mint"]; ent = seen.setdefault(mint, {})
        if grok_calls >= S["max_grok_calls_per_run"] or time.time() - t0 > S["grok_stop_after_s"]:
            stats["deferred"] += 1; continue          # not marked judged -> picked up next run
        budget_spent = budget["calls"] >= limit or budget["failed"] >= S["max_grok_failures_per_day"]
        src_used = "rugcheck"
        try:
            t = report(mint); kill = fact_kill(t)
        except Exception as e:                        # RugCheck down/erroring -> fallback check, else skip (never buy)
            rc_err = f"{type(e).__name__}: {str(e)[:100]}"
            try:
                t = fallback.report(mint, m.get("pair"), m.get("dex")); kill = fallback.fact_kill(t)
                src_used = t["source"]; stats["fallback"] += 1
            except Exception as e2:
                stats["rug"]["check_error"] = stats["rug"].get("check_error", 0) + 1
                if nowts - ent.get("rc_err_logged", 0) > S["rugcheck_error_relog_minutes"] * 60:
                    log(m, "rugcheck", reason=f"rugcheck_error: {rc_err} | fallback_error: {str(e2)[:150]}", sources=src)
                    ent["rc_err_logged"] = nowts
                time.sleep(sl); continue              # transient: retry next run, don't mark judged
        time.sleep(sl)
        if kill and src_used != "rugcheck":
            kill = "fb:" + kill
            if kill.endswith("_unknown"):            # data gap (often indexing lag): retry later, log at most hourly
                stats["rug"][kill] = stats["rug"].get(kill, 0) + 1
                if nowts - ent.get("rc_err_logged", 0) > S["rugcheck_error_relog_minutes"] * 60:
                    log(m, "rugcheck", reason=f"{kill} ({src_used}; {'; '.join(t.get('source_errors') or [])[:120]})", sources=src)
                    ent["rc_err_logged"] = nowts
                continue
        if kill:
            ent["judged"] = nowts; ent["symbol"] = m["symbol"]
            stats["rug"][kill] = stats["rug"].get(kill, 0) + 1
            log(m, "rugcheck", reason=kill, sources=src + ("" if src_used == "rugcheck" else f"|{src_used}")); continue
        if budget_spent:                              # free checks passed, but no Grok today -> never buy unjudged
            stats["deferred_budget"] += 1
            if nowts - ent.get("deferred_logged", 0) > S["deferred_relog_minutes"] * 60:
                log(m, "deferred", reason=f"grok_daily_budget_spent ({budget['calls']}/{limit})",
                    sources=src + ("" if src_used == "rugcheck" else f"|{src_used}"))
                ent["deferred_logged"] = nowts
            continue                                  # not marked judged -> Grok sees it once budget resets
        ent["symbol"] = m["symbol"]
        mk = {k: v for k, v in m.items() if k not in ("mint", "pair", "price_usd")}
        missing = [k for k in NEEDED_MARKET if m.get(k) is None] + \
                  [k for k in (NEEDED_TOKEN if src_used == "rugcheck" else NEEDED_TOKEN_FALLBACK) if t.get(k) is None]
        grok_calls += 1; stats["grok"] += 1
        verdict, conf, why, risk = "SKIP", "", "", ""
        tg = time.time()
        try:
            try:
                v, usage = ask(SCANNER_PROMPT, {"market": mk, "token": t}, timeout=(10, S["grok_timeout_s"]), with_usage=True)
            except NoResponse as e:                   # no completion came back: not counted, retry once later
                budget["failed"] += 1; save_budget(budget)
                ent["grok_fail"] = ent.get("grok_fail", 0) + 1
                if ent["grok_fail"] <= 1:
                    ent["grok_retry_at"] = nowts + S["grok_retry_after_minutes"] * 60
                    why = f"grok_no_response (retry in {S['grok_retry_after_minutes']}m): {e}"
                else:
                    ent["judged"] = nowts; why = f"grok_no_response (retry used): {e}"
                stats["grok_no_response"] = stats.get("grok_no_response", 0) + 1
                log(m, "grok", verdict="SKIP", reason=why, sources=src + ("" if src_used == "rugcheck" else f"|{src_used}"))
                say(f"grok [{src_used}] {m['symbol']} {mint[:8]}… -> no response after {time.time() - tg:.0f}s: {why}")
                time.sleep(sl); continue
            except Exception:
                budget["calls"] += 1; save_budget(budget)   # a response came back (e.g. bad JSON / 4xx): counts
                raise
            budget["calls"] += 1
            budget["cost_usd"] = round(budget["cost_usd"] + (usage.get("cost_in_usd_ticks") or 0) / 1e10, 6)
            save_budget(budget)
            ent["judged"] = nowts
            say(f"grok call {time.time() - tg:.1f}s model={usage.get('served_model')} "
                f"tokens={usage.get('prompt_tokens')}+{usage.get('completion_tokens')} "
                f"reasoning={(usage.get('completion_tokens_details') or {}).get('reasoning_tokens')}")
            verdict = str(v.get("verdict", "SKIP")).upper(); conf = v.get("confidence")
            why = v.get("why", ""); risk = v.get("main_risk", "")
            if verdict not in ("BUY", "SKIP"): verdict, why = "SKIP", f"bad verdict {verdict!r}: {why}"
            if verdict == "BUY" and missing: verdict, why = "SKIP", f"null fields {missing}: {why}"
            if verdict == "BUY" and (not isinstance(conf, (int, float)) or conf < MIN_CONFIDENCE):
                verdict, why = "SKIP", f"low confidence {conf}: {why}"
        except Exception as e:
            ent["judged"] = nowts
            verdict, why = "SKIP", f"grok_error: {type(e).__name__}: {str(e)[:150]}"
        stats[verdict] += 1
        log(m, "grok", verdict=verdict, confidence=conf, reason=why, main_risk=risk,
            sources=src + ("" if src_used == "rugcheck" else f"|{src_used}"))
        say(f"grok [{src_used}] {m['symbol']} {mint[:8]}… -> {verdict} ({conf}) {why} | risk: {risk}")
        if verdict == "BUY":
            pos = paper.open_position(mint, m["symbol"], m["pair"], m["price_usd"], conf, why)
            if pos: say(f"paper BUY ${S['paper_ticket_usd']} {m['symbol']} @ {m['price_usd']}")
        time.sleep(sl)
    save_seen(seen); save_budget(budget)
    say("run end " + json.dumps(stats) + f" elapsed={time.time() - t0:.0f}s")
    return stats

if __name__ == "__main__":
    lf = open(S["lock_file"], "w")
    try: fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: say("previous run still active, skipping"); sys.exit(0)
    try: run()
    except Exception:
        say("FATAL " + traceback.format_exc().replace("\n", " | ")); sys.exit(1)
