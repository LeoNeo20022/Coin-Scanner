# coin-scanner (LOG ONLY)

A paper-trade scanner for new low-cap Solana tokens. **It places no real trades, has no wallet,
holds no private keys, and never connects to any exchange, trading bot, or fomo account.**

Every 5 minutes (GitHub Actions cron, best effort) it:
1. Marks open *paper* positions to market with free DexScreener prices and applies the paper exits
   (-50% stop, sell half at +100%, close the rest after 72h).
2. Pulls new Solana tokens from DexScreener's free feeds and filters by market cap, liquidity, age,
   1h volume and buy/sell balance.
3. Runs RugCheck's free report on survivors (authority, danger flags, score, top-10 holders, LP lock,
   insider networks). If RugCheck errors, the token is skipped, never bought.
4. Asks Grok (xAI API) BUY/SKIP on what's left: at most 3 calls per run and 40 per UTC day.
   Once the daily budget is spent, candidates are logged as `deferred` and no Grok calls are made.
5. Logs everything and opens a $5 *paper* position on a BUY, then commits the state files back here.

Files written by the scanner: `scanner_log.csv`, `paper_trades.csv`, `seen.json`, `grok_budget.json`,
`scanner_runs.log`. Run `python summary.py` for a plain-English summary.

The only secret is `XAI_API_KEY`, stored as a GitHub Actions repository secret (never in the code).
