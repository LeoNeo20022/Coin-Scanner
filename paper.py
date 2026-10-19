# Paper positions only. Nothing here can trade: it reads public prices and rewrites a CSV.
import csv, os, time
from datetime import datetime, timezone
from config import SCANNER as S
import dexscreener as dx

FIELDS = ["id", "opened_at", "mint", "symbol", "pair", "entry_price", "entry_usd", "tokens_initial", "tokens_left",
          "half_taken", "status", "realized_usd", "last_price", "last_update", "unrealized_usd", "pnl_usd", "pnl_pct",
          "closed_at", "exit_reason", "events", "confidence", "why"]

def now(): return datetime.now(timezone.utc)
def iso(t): return t.isoformat(timespec="seconds")
def load(path=None):
    path = path or S["paper_file"]
    if not os.path.exists(path): return []
    with open(path, newline="") as f: return list(csv.DictReader(f))
def save(rows, path=None):
    path = path or S["paper_file"]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, path)

def _fee(): return S["fee_pct_per_side"] / 100.0

def _recalc(r):
    price, left = float(r["last_price"] or 0), float(r["tokens_left"] or 0)
    unreal = left * price * (1 - _fee())
    r["unrealized_usd"] = round(unreal, 4)
    pnl = float(r["realized_usd"] or 0) + unreal - float(r["entry_usd"])
    r["pnl_usd"] = round(pnl, 4); r["pnl_pct"] = round(100 * pnl / float(r["entry_usd"]), 2)

def open_position(mint, symbol, pair, price, confidence, why, path=None):
    rows = load(path)
    if any(r["mint"] == mint and r["status"] == "open" for r in rows): return None
    usd = float(S["paper_ticket_usd"])
    tokens = usd * (1 - _fee()) / price
    r = {"id": str(len(rows) + 1), "opened_at": iso(now()), "mint": mint, "symbol": symbol, "pair": pair,
         "entry_price": repr(price), "entry_usd": usd, "tokens_initial": repr(tokens), "tokens_left": repr(tokens),
         "half_taken": "0", "status": "open", "realized_usd": 0, "last_price": repr(price), "last_update": iso(now()),
         "closed_at": "", "exit_reason": "", "events": f"{iso(now())} BUY ${usd} @ {price}",
         "confidence": confidence, "why": why}
    _recalc(r); rows.append(r); save(rows, path)
    return r

def _sell(r, tokens, price, label):
    proceeds = tokens * price * (1 - _fee())
    r["realized_usd"] = round(float(r["realized_usd"] or 0) + proceeds, 6)
    r["tokens_left"] = repr(float(r["tokens_left"]) - tokens)
    r["events"] += f" | {iso(now())} {label} {tokens:.6g} @ {price} = ${proceeds:.4f}"

def update_open(sleep_s=1.0, path=None, tp_pct=None, tp_fraction=None, tag=""):
    """Mark open positions to market and apply exits. Returns list of event strings.
    Defaults = main strategy (TP half at +100%). The quick strategy passes its own file / TP (sell all at +50%)."""
    path = path or S["paper_file"]
    tp_pct = S["take_profit_pct"] if tp_pct is None else tp_pct
    tp_fraction = S["take_profit_fraction"] if tp_fraction is None else tp_fraction
    rows = load(path); events = []
    opn = [r for r in rows if r["status"] == "open"]
    if not opn: return events
    try:
        pairs = dx.all_pairs_for([r["mint"] for r in opn], sleep_s)
    except Exception as e:
        return [f"price_update_error: {e}"]
    t = now()
    for r in opn:
        ps = pairs.get(r["mint"]) or []
        p = next((x for x in ps if x.get("pairAddress") == r["pair"]), None) or \
            max(ps, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0, default=None)
        price = None
        if p and p.get("priceUsd"):
            try: price = float(p["priceUsd"])
            except ValueError: price = None
        if price is not None:
            r["last_price"] = repr(price); r["last_update"] = iso(t)
        else:
            events.append(f"{r['symbol']}: no price this run (kept last {r['last_price']})")
            price = float(r["last_price"])
        entry = float(r["entry_price"]); chg = 100 * (price / entry - 1)
        age_h = (t - datetime.fromisoformat(r["opened_at"])).total_seconds() / 3600
        reason = None
        if r["half_taken"] == "0" and chg >= tp_pct:
            if tp_fraction >= 1:   # quick strategy: whole position out at TP
                _sell(r, float(r["tokens_left"]), price, f"CLOSE_take_profit({chg:+.0f}%)")
                r["half_taken"] = "1"; r["tokens_left"] = "0"; r["status"] = "closed"; r["closed_at"] = iso(t)
                r["exit_reason"] = "take_profit"; events.append(f"{tag}{r['symbol']}: closed (take_profit) at {chg:+.1f}%")
                _recalc(r); continue
            _sell(r, float(r["tokens_left"]) * tp_fraction, price, f"TP_HALF({chg:+.0f}%)")
            r["half_taken"] = "1"; events.append(f"{tag}{r['symbol']}: take-profit half at {chg:+.1f}%")
        if chg <= S["stop_loss_pct"]: reason = "stop_loss"
        elif age_h >= S["max_hold_hours"]: reason = "time_exit_72h"
        if reason:
            _sell(r, float(r["tokens_left"]), price, f"CLOSE_{reason}({chg:+.0f}%)")
            r["tokens_left"] = "0"; r["status"] = "closed"; r["closed_at"] = iso(t); r["exit_reason"] = reason
            events.append(f"{tag}{r['symbol']}: closed ({reason}) at {chg:+.1f}%")
        _recalc(r)
    save(rows, path)
    return events
