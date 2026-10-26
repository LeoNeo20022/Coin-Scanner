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
    "fee_pct_per_side": 0,              # optional simulated swap fee per buy/sell, e.g. 1 = 1%
    # files
    "seen_file": "seen.json", "log_file": "scanner_log.csv", "paper_file": "paper_trades.csv",
    "lock_file": "scanner.lock",
}

# ---- SHADOW paper strategies (see shadow.py): each new main paper buy is mirrored into each, own CSV,
#      own pretend $100 budget in summary.py. Exits judged on 1-min candles (SL first if both in one candle). ----
SHADOW = {
    "target5": {"file": "paper_trades_target5.csv", "tp_pct": 50, "sl_pct": SCANNER["stop_loss_pct"],
                "max_hold_min": SCANNER["max_hold_hours"] * 60},   # +$5 on $10; main stop loss and max hold
    "scalp":   {"file": "paper_trades_scalp.csv", "tp_pct": 20, "sl_pct": -10, "max_hold_min": 60},  # +$2 / -$1, 60 min
}

# ---- SECOND LOOK (see second_look.py): re-check a sample of REJECTED coins ~24h and ~48h later with the same free
#      checks (market filter + RugCheck/fallback, NO Grok). Coins that would pass get one hypothetical $10 paper buy,
#      scored under hold / target5 / scalp on 1-min candles, each with its own pretend $100. No fees/slippage. ----
SECOND_LOOK = {
    "queue_file": "second_look_queue.json", "check_file": "second_look.csv",
    "paper_files": {"hold": "second_look_hold.csv", "target5": "second_look_target5.csv", "scalp": "second_look_scalp.csv"},
    "checkpoints": {"24h": 24, "48h": 48},
    "max_new_per_day": 50,             # new queue entries per UTC day
    "max_per_reason_per_day": 6,       # variety: e.g. at most 6 'market:liquidity_low' per day
    "max_market_per_hour": 3,          # market-filter rejects spread across the day, also max 1 per reason per hour (rugcheck/grok exempt)
    "skip_reasons": ["no_solana_pair", "missing:", "too_old", "mcap_too_high", "rugcheck_error", "grok_no_response",
                     "grok_error", "fb:authority_unknown", "fb:transfer_unknown", "fb:top10_unknown", "fb:lp_unknown"],
    "waive_max_age": True,             # a coin is always older at recheck; judge it on everything except the 72h age cap
    "dead_liquidity_usd": 1_000, "dead_volume_h24_usd": 100,   # 'dead' = no pair/price, liquidity < $1K, 24h volume < $100, or -90%
    "max_rechecks_per_scan": 8, "max_paper_coins_per_scan": 12, "max_seconds_per_scan": 90,
    "queue_keep_days": 5,
}

# ---- LAUNCH (see launch.py): paper-test coins still on the pump.fun BONDING CURVE (not graduated; the scanner
#      rejects these as 'pumpfun_bonding_curve'). Capped sample that passes the basic checks possible before
#      graduation (RugCheck report required, NO Grok). $10 paper buy at the DexScreener price at detection, scored on
#      GeckoTerminal 1-min candles of the curve pool (then the PumpSwap pool after graduation). No fees/slippage. ----
# Oct 23 2026: LAUNCH dropped by owner. New launch paper buys OFF; open launch trades are still exit-checked to close.
LAUNCH_ENABLED = False
LAUNCH = {
    "inbox_file": "launch_inbox.json", "state_file": "launch_state.json", "check_file": "launch_checks.csv",
    "paper_files": {"scalp": "launch_scalp.csv", "target5": "launch_target5.csv", "hold": "launch_hold.csv",
                    "flash": "launch_flash.csv"},
    # name -> (tp_pct, tp_fraction, sl_pct, max_hold_min); hold = main rules (half at +100%, -50% stop, 72h)
    # flash (added Oct 22): fastest in-and-out, +10% / -7% / 15 min, same entries as the others (own pretend $100)
    "strategies": {"scalp": (20, 1.0, -10, 60), "target5": (50, 1.0, -50, 24 * 60), "flash": (10, 1.0, -7, 15),
                   "hold": (SCANNER["take_profit_pct"], SCANNER["take_profit_fraction"], SCANNER["stop_loss_pct"],
                            SCANNER["max_hold_hours"] * 60)},
    # pre-graduation market filter (DexScreener pumpfun pair)
    "min_age_min": 5, "max_age_hours": 24,
    "min_mcap_usd": 8_000, "min_volume_h1_usd": 5_000, "min_buys_h1": 40, "min_buy_sell_ratio_h1": 1.0,
    "min_price_change_m5_pct": -15,            # skip coins dumping right now
    # curve + token checks (RugCheck report is REQUIRED; no report -> no buy)
    "min_curve_progress_pct": 15, "max_curve_progress_pct": 97,
    "max_risk_score": 50, "min_holders": 50,
    "max_top_holder_pct": 8, "max_top10_pct": 30,     # excluding the bonding-curve account itself
    "max_creator_pct": 5, "max_insider_network_pct": 10,
    "min_ath_ratio": 0.5,                      # pump.fun (optional source): skip if mcap < 50% of its ATH mcap
    # pacing / caps
    "max_buys_per_day": 24, "max_buys_per_hour": 3, "max_buys_per_scan": 1,
    "max_rugchecks_per_scan": 6, "reeval_after_min": 15, "max_evals_per_mint": 6,
    "max_paper_coins_per_scan": 15, "max_seconds_per_scan": 100,
    "curve_initial_real_tokens": 793_100_000, "curve_virtual_sol_offset": 30,   # pump.fun curve constants (impact estimate)
    "state_keep_days": 3,
    "flash_catchup_hours": 3,      # launch.py copies scalp entries missing from launch_flash.csv if this recent (older: backfill script)
}

# ---- ESTIMATED LIVE FEES (display only, see fees.py): applied in summary.py ALONGSIDE the unchanged paper numbers,
#      assuming live trades go through Jupiter direct. Exit logic is NOT affected. ----
FEES = {
    "pool_pct_default": 1.25,                              # unknown / small pools
    "pool_pct_by_dex": {"pumpfun": 1.25, "pumpswap": 1.1},  # PumpSwap ~1.0-1.2% in the scanner's $50K-$2M range
    "launch_pool_pct": 1.25,                               # bonding curve (and low-cap PumpSwap after graduation)
    "jup_pct": 0.10, "jup_new_token_pct": 0.50, "new_token_hours": 24,   # Jupiter /order platform fee per side
    "network_usd_per_side": 0.003,                         # base + priority fee per transaction (rent assumed refunded)
}

# ---- SCOUT strategies (see scout.py, added Oct 26 2026): two LOG-ONLY paper sources scouted on X by Theo.
#      A "pool"  = POOL-GRADUATED FILTER (@JnoAYx): graduated pool, 24h vol >= $40K, mcap $60K-$8M, never in the first
#                  minutes; his exit +50% with a -30% / -50% stop (72h), plus our hold / target5 / scalp.
#      B "wait3" = WAIT-3 PULLBACK (@malangeeSOL, no proof): never up 5x from the pool's first price, 30-50% pullback from
#                  the high, then a higher low with volume holding; ladder 50% at 2x / 25% at 5x / 25% at 10x, stop at the
#                  pullback low, 24h time stop (72h variant too), plus our hold / target5 / scalp.
#      Every paper buy first passes RugCheck (or the free fallback) AND a read-only Jupiter sell quote (~$15 -> SOL,
#      no wallet): skipped if no route or the sell loses > max_sell_loss_pct beyond the pool fee. $10 per paper buy.
#      Fees in the CSVs: pool fee/side (1.25%, 1.1% PumpSwap) + $0.003 network/side, no Jupiter fee, no slippage. ----
SCOUT = {
    "state_file": "scout_state.json", "check_file": "scout_checks.csv",
    "trade_files": {"pool": "pool_filter_trades.csv", "wait3": "wait3_trades.csv"},
    "ticket_usd": 10, "budget_usd": 100, "network_usd_per_side": 0.003,
    "pool_pct_by_dex": {"pumpswap": 1.1}, "pool_pct_default": 1.25,
    # watchlist (candidates): GeckoTerminal trending / top PumpSwap / new pools + this iteration's scanner discoveries
    # + optional scout_watch.json (e.g. caller / influencer mints pushed by the box). Kept 48h from first sight.
    "watch_hours": 48, "max_watch": 350,
    "gt_feeds": ["trending_pools?page=1", "trending_pools?page=1&duration=1h",
                 "dexes/pumpswap/pools?page=1&sort=h24_volume_usd_desc", "new_pools?page=1"],
    "gt_feed_min_reserve_usd": 10_000,
    # A: pool-graduated filter
    "a_min_vol24_usd": 40_000, "a_min_mcap_usd": 60_000, "a_max_mcap_usd": 8_000_000,
    "a_min_liquidity_usd": 10_000, "a_min_pool_age_min": 15,
    "a_max_per_day": 10, "a_slot_hours": 2,          # <= 1 new A buy per 2h slot (UTC), <= 10 per UTC day
    "a_rebuy_days": 7, "a_fail_cooldown_h": 6, "a_max_attempts_per_scan": 3,
    # B: WAIT-3 pullback (5-min candles from the pool start)
    "b_max_pool_age_h": 72, "b_min_pool_age_min": 30, "b_min_liquidity_usd": 10_000, "b_min_vol24_usd": 20_000,
    "b_min_mcap_usd": 30_000, "b_eval_every_min": 25, "b_evals_per_scan": 6, "b_signal_max_age_min": 35,
    "b_max_chase_pct": 10, "b_max_per_day": 10,
    "w3": {"max_run_x": 5.0, "pull_min": 0.30, "pull_max": 0.50, "bounce_pct": 10, "hl_min_pct": 2,
           "min_dip_pct": 5, "max_entry_vs_high": 0.85, "vol_hold_ratio": 0.5, "min_vol_1h_usd": 5_000},
    # liquidity (real sell depth) check: read-only Jupiter quote, token -> SOL
    "liq_quote_usd": 15, "max_sell_loss_pct": 5.0, "max_jup_impact_pct": 5.0,
    "jup_quote_url": "https://lite-api.jup.ag/swap/v1/quote", "jup_price_url": "https://lite-api.jup.ag/price/v3",
    # pacing
    "max_update_coins_per_scan": 10, "max_seconds_per_scan": 110, "sleep_s": 1.2,
}
