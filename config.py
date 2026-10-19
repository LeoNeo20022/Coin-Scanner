MODEL = "grok-4"            # check console.x.ai -> Models if the API rejects it
TICKET_USD = 50
TRADER = {"min_closed_trades": 20, "min_win_rate": 0.55, "max_top_coin_share": 0.50, "min_pnl_7d_usd": 0}
TOKEN = {"max_risk_score": 50, "max_top10_pct": 30, "min_lp_locked_pct": 90, "max_insider_nets": 0,
         "max_single_holder_pct": 5}   # single non-pool wallet cap (used by the fallback check)
# Fallback safety check when RugCheck errors (free, no keys). See fallback.py.
FALLBACK = {"goplus_url": "https://api.gopluslabs.io/api/v1/solana/token_security",
            "jupiter_url": "https://lite-api.jup.ag/tokens/v2/search",
            "rpc_url": "https://api.mainnet-beta.solana.com",
            "max_transfer_fee_pct": 0,              # any Token-2022 transfer fee = kill
            "top10_cap_when_holders_unknown": 20}   # stricter top-10 cap when no per-wallet holder list
MIN_CONFIDENCE = 0.65
LEADERS_FILE = "leaders.json"
LOG_FILE = "checks.csv"

# ---- LOW-CAP SCANNER (log-only, paper trades) ----
SCANNER = {
    # candidate filter (DexScreener pair data)
    "min_mcap_usd": 50_000, "max_mcap_usd": 2_000_000,   # marketCap, falls back to fdv
    "min_liquidity_usd": 20_000,
    "min_age_hours": 1, "max_age_hours": 72,
    "min_volume_h1_usd": 10_000,
    "require_buys_gt_sells_h1": True,
    # dedupe / pacing
    "rejudge_after_hours": 24,          # a mint that reached rugcheck is not re-judged for this long
    "market_relog_hours": 24,           # a mint failing the market filter is logged at most once per this window
    "max_grok_calls_per_run": 3,
    "max_grok_calls_per_day": 40,       # total Grok calls per UTC day (env GROK_DAILY_BUDGET overrides); then free checks only
    "grok_budget_file": "grok_budget.json",
    "grok_stop_after_s": 150,           # don't START a new Grok call after this many seconds into a run (GitHub job timeout)
    "rugcheck_error_relog_minutes": 60, # log a RugCheck error for the same mint at most once per this window
    "deferred_relog_minutes": 60,       # log a budget-deferred candidate at most once per this window
    "run_interval_s": 300,              # schedule interval, used by summary.py coverage (GitHub cron */5)
    "sleep_between_calls_s": 1.5,       # between rugcheck / Grok / DexScreener calls
    "grok_timeout_s": 60,            # READ timeout per call (connect timeout 10s); normal latency 6-9s
    "grok_retry_after_minutes": 60,     # a coin whose Grok call got no response is retried once after this
    "max_grok_failures_per_day": 10,    # no-response failures (not counted as calls) before Grok pauses for the day
    # paper trading
    "paper_ticket_usd": 10,           # new paper trades from 2026-10-19 (earlier ones were $5; each row keeps its own entry_usd)
    "paper_budget_usd": 100,            # pretend paper bankroll shown in summary.py (display only)
    "stop_loss_pct": -50,               # close everything at -50% from entry (or the remaining half after TP)
    "take_profit_pct": 100,             # sell half at +100%
    "take_profit_fraction": 0.5,
    "max_hold_hours": 72,               # close whatever is left after this
    "quick_take_profit_pct": 50,        # QUICK shadow strategy: sell ALL at +50% (+$5 on $10); same stop_loss / max_hold
    "fee_pct_per_side": 0,              # optional simulated swap fee per buy/sell, e.g. 1 = 1%
    # files
    "seen_file": "seen.json", "log_file": "scanner_log.csv", "paper_file": "paper_trades.csv",
    "quick_paper_file": "paper_trades_quick.csv",
    "lock_file": "scanner.lock",
}
