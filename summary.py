# Plain-English summary of the scanner's log and paper trades. Read-only.
import csv, os
from collections import Counter
from datetime import datetime, timezone, timedelta
from config import SCANNER as S

def rows(path):
    if not os.path.exists(path): return []
    with open(path, newline="") as f: return list(csv.DictReader(f))
def ts(s): return datetime.fromisoformat(s)
def money(x): return f"{'-' if x < 0 else '+'}${abs(x):.2f}"

def section(title, log, trades):
    print(f"\n=== {title} ===")
    mints = {r["mint"] for r in log}
    print(f"Candidates logged: {len(log)} rows covering {len(mints)} different tokens")
    stage = Counter(r["stage"] for r in log)
    mk = Counter(r["reason"] for r in log if r["stage"] == "market")
    rk = Counter(r["reason"].split(":")[0] if r["reason"].startswith("rugcheck_error") else r["reason"]
                 for r in log if r["stage"] == "rugcheck")
    gk = [r for r in log if r["stage"] == "grok"]
    print(f"  Died at market filter: {stage.get('market', 0)}" + (f"  ({', '.join(f'{k} {v}' for k, v in mk.most_common())})" if mk else ""))
    if stage.get("deferred"): print(f"  Deferred (Grok daily budget spent): {stage['deferred']}")
    print(f"  Died at rug check:     {stage.get('rugcheck', 0)}" + (f"  ({', '.join(f'{k} {v}' for k, v in rk.most_common())})" if rk else ""))
    buys = [r for r in gk if r["verdict"] == "BUY"]
    errs = sum(1 for r in gk if r["reason"].startswith("grok_error"))
    print(f"  Judged by Grok:        {len(gk)}  -> BUY {len(buys)}, SKIP {len(gk) - len(buys)}" + (f" ({errs} were errors)" if errs else ""))
    for r in buys: print(f"     BUY {r['symbol']} (conf {r['confidence']}): {r['reason']}")
    if not trades:
        print("Paper trades: none opened in this window."); return
    real = sum(float(t["realized_usd"] or 0) for t in trades)
    unreal = sum(float(t["unrealized_usd"] or 0) for t in trades if t["status"] == "open")
    cost = sum(float(t["entry_usd"]) for t in trades)
    pnl = sum(float(t["pnl_usd"] or 0) for t in trades)
    closed = [t for t in trades if t["status"] == "closed"]
    realized_pnl = sum(float(t["pnl_usd"] or 0) for t in closed)
    open_pnl = sum(float(t["pnl_usd"] or 0) for t in trades if t["status"] == "open")
    print(f"Paper trades: {len(trades)} opened (${cost:.2f} hypothetically put in), {len(closed)} closed, {len(trades) - len(closed)} open")
    print(f"  Total paper P&L: {money(pnl)} ({100 * pnl / cost:+.1f}%)  = closed {money(realized_pnl)} + open {money(open_pnl)} (unrealized)")
    w = sum(1 for t in trades if float(t["pnl_usd"] or 0) > 0); l = sum(1 for t in trades if float(t["pnl_usd"] or 0) <= 0)
    print(f"  Winners / losers (incl. open, marked to market): {w} / {l}")
    for t in sorted(trades, key=lambda t: float(t["pnl_usd"] or 0), reverse=True):
        st = t["status"] if t["status"] == "open" else f"closed: {t['exit_reason']}"
        half = ", took half profit" if t["half_taken"] == "1" else ""
        print(f"    #{t['id']} {t['symbol']:<12} ${float(t['entry_usd']):.0f} in, {money(float(t['pnl_usd'] or 0)):>8} ({float(t['pnl_pct'] or 0):+.0f}%)  [{st}{half}]  opened {t['opened_at'][:16]}")
    b = max(trades, key=lambda t: float(t["pnl_usd"] or 0)); wst = min(trades, key=lambda t: float(t["pnl_usd"] or 0))
    print(f"  Best: {b['symbol']} {money(float(b['pnl_usd']))}   Worst: {wst['symbol']} {money(float(wst['pnl_usd']))}")

def coverage(cut):
    # How many 15-min scanner runs actually happened (the loop only runs while the box is awake).
    path = "scanner_runs.log"
    if not os.path.exists(path): return
    starts = []
    for line in open(path):
        if "run start" in line and line.startswith("["):
            try: starts.append(datetime.fromisoformat(line[1:line.index("]")]))
            except ValueError: pass
    if not starts: return
    now = datetime.now(timezone.utc)
    recent = [t for t in starts if t >= cut]
    window_start = max(cut, starts[0])
    expected = max(1, int((now - window_start).total_seconds() // S.get("run_interval_s", 900)))
    pts = recent + [now]
    gaps = [(b - a, a) for a, b in zip(pts, pts[1:])]
    big = max(gaps, key=lambda g: g[0]) if gaps else None
    msg = f"Scanner coverage (last 24h): {len(recent)} of about {expected} scheduled {S.get('run_interval_s', 900) // 60}-min runs happened"
    if big and big[0] > timedelta(seconds=3 * S.get("run_interval_s", 900)):
        msg += f"; longest gap {big[0].total_seconds() / 3600:.1f}h starting {big[1].strftime('%m-%d %H:%M')} UTC"
    print(msg)
    print(f"Last scanner run: {starts[-1].strftime('%m-%d %H:%M')} UTC")

def budget(trades, label="Pretend", quiet=False):
    """Pretend paper bankroll (display only): each trade counts with its own size (old $5, new $10).
    Each strategy has its OWN independent pretend budget."""
    start = float(S.get("paper_budget_usd", 100))
    pnl = sum(float(t["pnl_usd"] or 0) for t in trades)
    in_use = sum(float(t["entry_usd"]) for t in trades if t["status"] == "open")
    bal = start + pnl
    line = (f"{label} ${start:.0f} paper budget: now worth ${bal:.2f} ({money(pnl)}, {100 * pnl / start:+.1f}%), "
            f"${in_use:.2f} in {sum(1 for t in trades if t['status'] == 'open')} open trades, ${bal - in_use:.2f} free (approx)")
    if not quiet: print("\n" + line)
    return line

def breakeven(tp_pct, sl_pct):
    """Win rate needed to break even when every win is +tp% and every loss is sl% (negative)."""
    return 100 * abs(sl_pct) / (tp_pct + abs(sl_pct))

def strat_stats(trades, win_test):
    closed = [t for t in trades if t["status"] == "closed"]
    wins = [t for t in closed if win_test(t)]
    pnl = sum(float(t["pnl_usd"] or 0) for t in trades)
    wr = f"{100 * len(wins) / len(closed):.0f}%" if closed else "n/a"
    return closed, wins, pnl, wr

def shadow_sections(main_trades):
    from config import SHADOW
    start = float(S.get("paper_budget_usd", 100))
    fr, mtp, msl = S["take_profit_fraction"], S["take_profit_pct"], S["stop_loss_pct"]
    worst_win = fr * mtp + (1 - fr) * msl
    table = []
    closed, wins, pnl, wr = strat_stats(main_trades, lambda t: float(t["pnl_usd"] or 0) > 0)
    table.append(("main/hold", len(main_trades), len(closed), len(wins), wr, pnl,
                  f"~{breakeven(worst_win, msl):.0f}% (if winners only get half at +{mtp}% then stop)"))
    for name, st in SHADOW.items():
        trades = rows(st["file"])
        print(f"\n=== {name.upper()} shadow strategy: sell all at +{st['tp_pct']}%, stop {st['sl_pct']}%, max hold "
              f"{st['max_hold_min'] / 60:g}h (1-min candles, SL first if both in one candle) ===")
        closed, wins, pnl, wr = strat_stats(trades, lambda t: t["exit_reason"] == "take_profit")
        be = breakeven(st["tp_pct"], st["sl_pct"])
        table.append((name, len(trades), len(closed), len(wins), wr, pnl, f"{be:.0f}%"))
        if not trades:
            print("No trades yet (starts with the next $10 scanner paper buy)."); continue
        tex = sum(1 for t in closed if t["exit_reason"] == "time_exit")
        print(f"  {len(trades)} opened, {len(closed)} closed, {len(trades) - len(closed)} open | hit target {len(wins)}, "
              f"stopped {sum(1 for t in closed if t['exit_reason'] == 'stop_loss')}, time exits {tex} | win rate {wr} | P&L {money(pnl)}")
        for t in sorted(trades, key=lambda t: t["opened_at"]):
            stt = t["status"] if t["status"] == "open" else f"closed: {t['exit_reason']}"
            src = " [snapshot price]" if t.get("price_source") == "snapshot" else ""
            print(f"    #{t['id']} {t['symbol']:<12} ${float(t['entry_usd']):.0f} in, {money(float(t['pnl_usd'] or 0)):>8} "
                  f"({float(t['pnl_pct'] or 0):+.0f}%)  [{stt}]{src}  opened {t['opened_at'][:16]}")
        mains = {t["mint"]: t for t in main_trades}
        same = [t for t in trades if t["mint"] in mains]
        if same:
            print(f"  Same {len(same)} coins under main/hold: {money(sum(float(mains[t['mint']]['pnl_usd'] or 0) for t in same))} "
                  f"vs {name} {money(sum(float(t['pnl_usd'] or 0) for t in same))}")
    table += second_look_section()
    try: table += launch_section()
    except Exception as e: print(f"\n(LAUNCH section error: {e})")
    print(f"\n=== Strategies side by side (each has its own pretend ${start:.0f}) ===")
    print(f"  {'strategy':<10} {'trades':>6} {'closed':>6} {'wins':>5} {'win%':>5} {'P&L':>9} {'budget':>9}  break-even win rate")
    for name, n, c, w, wr, pnl, be in table:
        print(f"  {name:<10} {n:>6} {c:>6} {w:>5} {wr:>5} {money(pnl):>9} {'$%.2f' % (start + pnl):>9}  {be}")

def second_look_section():
    """SECOND LOOK: sampled rejected coins re-checked ~24h / ~48h later (free checks only, no Grok). Returns side-by-side rows."""
    import json
    from config import SECOND_LOOK as C, SHADOW
    print("\n=== SECOND LOOK: rejected coins re-checked ~24h and ~48h later (same free checks, no Grok) ===")
    try: q = json.load(open(C["queue_file"]))
    except Exception: q = {"entries": {}}
    ents = list(q.get("entries", {}).values()); checks = rows(C["check_file"])
    cut = datetime.now(timezone.utc) - timedelta(hours=24)
    new24 = sum(1 for e in ents if ts(e["rejected_at"]) >= cut)
    print(f"Queued: {len(ents)} coins in the queue ({new24} added in the last 24h)"
          + (f"; by reject reason: {', '.join(f'{k} {v}' for k, v in Counter(e['reason_key'] for e in ents).most_common(8))}" if ents else ""))
    waiting = [e for e in ents if not e.get("done")]
    if waiting:
        nxt = min(e["rejected_ts"] + (48 if "24h" in e.get("checks", {}) else 24) * 3600 for e in waiting)
        print(f"  Waiting: {len(waiting)}; next recheck due ~{datetime.fromtimestamp(nxt, timezone.utc).strftime('%m-%d %H:%M')} UTC")
    for cp in C["checkpoints"]:
        cs = [r for r in checks if r["checkpoint"] == cp]
        if not cs: print(f"  {cp} rechecks: none yet"); continue
        ok = [r for r in cs if r["would_pass"] == "1"]; dead = sum(1 for r in cs if r["dead"] == "1")
        fails = Counter((r["safety_result"] if r["market_result"] == "pass" else r["market_result"]) for r in cs if r["would_pass"] != "1")
        up = [float(r["price_change_vs_reject_pct"]) for r in cs if r["price_change_vs_reject_pct"] not in ("", None)]
        med = sorted(up)[len(up) // 2] if up else None
        print(f"  {cp} rechecks: {len(cs)} -> would pass now {len(ok)} ({100 * len(ok) / len(cs):.0f}%), dead {dead}"
              + (f", median price vs reject {med:+.0f}%" if med is not None else ""))
        if fails: print(f"     top fail reasons: {', '.join(f'{k} {v}' for k, v in fails.most_common(6))}")
        for r in ok: print(f"     PASS {r['symbol']} (was rejected {r['reject_stage']}: {r['reject_reason'][:50]}), price vs reject {r['price_change_vs_reject_pct'] or '?'}%")
    out = []; start = float(S.get("paper_budget_usd", 100))
    fr, mtp, msl = S["take_profit_fraction"], S["take_profit_pct"], S["stop_loss_pct"]
    bes = {"hold": f"~{breakeven(fr * mtp + (1 - fr) * msl, msl):.0f}%",
           "target5": f"{breakeven(SHADOW['target5']['tp_pct'], SHADOW['target5']['sl_pct']):.0f}%",
           "scalp": f"{breakeven(SHADOW['scalp']['tp_pct'], SHADOW['scalp']['sl_pct']):.0f}%"}
    print(f"  Hypothetical $10 paper buys at the recheck price (one per coin), own pretend ${start:.0f} each, 1-min candles, no fees/slippage:")
    for name, path in C["paper_files"].items():
        tr = rows(path)
        test = (lambda t: float(t["pnl_usd"] or 0) > 0) if name == "hold" else (lambda t: t["exit_reason"] == "take_profit")
        closed, wins, pnl, wr = strat_stats(tr, test)
        print(f"     {name:<8} {len(tr)} buys, {len(closed)} closed, {len(wins)} wins, P&L {money(pnl)}, budget ${start + pnl:.2f}")
        out.append((f"2nd:{name}", len(tr), len(closed), len(wins), wr, pnl, bes[name]))
    return out

def launch_section():
    """LAUNCH: pump.fun bonding-curve (pre-graduation) coins, free checks only, quick exits. Returns side-by-side rows."""
    import json
    from config import LAUNCH as L
    print("\n=== LAUNCH: pump.fun coins still on the bonding curve (pre-graduation), free checks only, no Grok ===")
    try: st = json.load(open(L["state_file"]))
    except Exception: st = {"mints": {}, "days": {}}
    checks = rows(L["check_file"]); cut = datetime.now(timezone.utc) - timedelta(hours=24)
    c24 = [r for r in checks if ts(r["time"]) >= cut]
    seen24 = sum(1 for e in st.get("mints", {}).values() if e.get("first", 0) >= cut.timestamp())
    print(f"Bonding-curve coins seen (last 24h): {seen24}; RugCheck-checked: {len(c24)}, passed: {sum(1 for r in c24 if r['result'] == 'pass')}")
    fails = Counter()
    for d, v in st.get("days", {}).items():
        if d >= cut.strftime("%Y-%m-%d"): fails.update(v.get("fails", {}))
    if fails: print(f"  Top fail reasons (today+yesterday UTC, incl. repeat looks): {', '.join(f'{k} {v}' for k, v in fails.most_common(8))}")
    out = []; start = float(S.get("paper_budget_usd", 100))
    print(f"  $10 paper buys at the price at detection, own pretend ${start:.0f} each, 1-min candles (curve pool, then PumpSwap after"
          f" graduation). NO fees/slippage simulated (real pump.fun trades pay fees each side + latency/MEV):")
    for name, path in L["paper_files"].items():
        tp, fr, sl, mh = L["strategies"][name]; tr = rows(path)
        test = (lambda t: float(t["pnl_usd"] or 0) > 0) if fr < 1 else (lambda t: t["exit_reason"] == "take_profit")
        closed, wins, pnl, wr = strat_stats(tr, test)
        be = f"~{breakeven(fr * tp + (1 - fr) * sl, sl):.0f}%" if fr < 1 else f"{breakeven(tp, sl):.0f}%"
        grad = sum(1 for t in tr if t.get("graduated_at")); snap = sum(1 for t in tr if t.get("price_source") == "snapshot")
        print(f"     {name:<8} (+{tp}%{' half' if fr < 1 else ''} / {sl}% / {mh / 60:g}h) {len(tr)} buys, {len(closed)} closed, "
              f"{len(wins)} wins ({wr}), P&L {money(pnl)}, budget ${start + pnl:.2f}"
              + (f", {grad} graduated" if grad else "") + (f", {snap} on snapshot prices" if snap else ""))
        out.append((f"lch:{name}", len(tr), len(closed), len(wins), wr, pnl, be))
    for t in sorted(rows(L["paper_files"]["scalp"]), key=lambda t: t["opened_at"])[-10:]:
        stt = t["status"] if t["status"] == "open" else f"closed: {t['exit_reason']}"
        print(f"    #{t['id']} {t['symbol']:<12} scalp {money(float(t['pnl_usd'] or 0)):>8} ({float(t['pnl_pct'] or 0):+.0f}%) [{stt}] "
              f"curve {t.get('curve_progress_pct') or '?'}%  opened {t['opened_at'][:16]}")
    return out

def main():
    log, trades = rows(S["log_file"]), rows(S["paper_file"])
    cut = datetime.now(timezone.utc) - timedelta(hours=24)
    print("LOW-CAP SCANNER SUMMARY (paper only, no real money)")
    if trades: print(f"Prices last updated: {max(t['last_update'] for t in trades)[:16]} UTC")
    coverage(cut)
    section("Last 24 hours", [r for r in log if ts(r["time"]) >= cut], [t for t in trades if ts(t["opened_at"]) >= cut])
    section("All time", log, trades)
    budget(trades, "Main/hold")
    shadow_sections(trades)
    print("\nNote: market-filter rejects are logged once per token per day; P&L uses prices sampled every scheduled run, no fees unless set in config.")

if __name__ == "__main__":
    main()
