# coin-scanner (LOG ONLY)

A paper-trade scanner for new low-cap Solana tokens. **It places no real trades, has no wallet,
holds no private keys, and never connects to any exchange, trading bot, or fomo account.**

Every 5 minutes it does the steps below. GitHub only starts the scheduled run every ~30-36 min, so each run
(`loop.sh`) scans every 5 min for ~38 min and commits state after each scan. Each scan:
1. Marks open *paper* positions to market with free DexScreener prices and applies the paper exits
   (-50% stop, sell half at +100%, close the rest after 72h).
2. Pulls new Solana tokens from DexScreener's free feeds and filters by market cap, liquidity, age,
   1h volume and buy/sell balance.
3. Runs RugCheck's free report on survivors (authority, danger flags, score, top-10 holders, LP lock,
   insider networks). If RugCheck errors, a fallback check (`fallback.py`: GoPlus + Jupiter, then public
   Solana RPC) applies the same kill rules plus "no single wallet > 5%". If every source fails, the token
   is skipped, never bought.
4. Asks Grok (xAI API) BUY/SKIP on what's left: at most 3 calls per run and 40 per UTC day.
   Once the daily budget is spent, candidates are logged as `deferred` and no Grok calls are made.
5. Logs everything and opens a $5 *paper* position on a BUY, then commits the state files back here.

**Second look** (`second_look.py`, run after each scan): a sample of rejected coins (max 50 new per UTC day,
max 6 per reject reason, market rejects spread over the day; deduped by mint) is queued in `second_look_queue.json`
and re-checked ~24h and ~48h later with the same free checks (market filter except the 72h age cap, RugCheck +
fallback). No Grok calls. Results go to `second_look.csv`. A coin that would pass gets one hypothetical $10 paper
buy at the recheck price, scored on 1-min candles under hold / target5 / scalp (`second_look_{hold,target5,scalp}.csv`,
own pretend $100 each, no fees/slippage). `summary.py` has a SECOND LOOK section.

Files written by the scanner: `scanner_log.csv`, `paper_trades.csv`, `seen.json`, `grok_budget.json`,
`scanner_runs.log`. Run `python summary.py` for a plain-English summary.

The only secret is `XAI_API_KEY`, stored as a GitHub Actions repository secret (never in the code).

## LAUNCH paper category (launch.py, paper only)
Coins still on the pump.fun bonding curve (the scanner rejects them as `pumpfun_bonding_curve`) are handed to
`launch.py` via `launch_inbox.json`. A capped sample (max 24/UTC day, 3/hour, 1 per scan) that passes the
pre-graduation checks in `config.LAUNCH` (DexScreener activity filter + a required RugCheck report: authorities off,
no danger risks, no transfer fee, holder/creator/insider caps excluding the curve account, curve progress 15-97%,
50+ holders; pump.fun's public coin endpoint for graduated/banned/ATH when reachable) gets one $10 paper buy at the
DexScreener price at detection. Scored on GeckoTerminal 1-min candles of the curve pool (then the PumpSwap pool after
graduation) under scalp (+20/-10/60m), target5 (+50/-50/24h) and hold (main rules), each with its own pretend $100.
Files: launch_checks.csv, launch_state.json, launch_{scalp,target5,hold}.csv. No Grok, no fees/slippage simulated.
