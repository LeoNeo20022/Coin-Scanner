#!/bin/bash
# Runs inside one GitHub Actions job: scan, commit state, wait until 5 min after the previous scan START, repeat.
# GitHub only starts scheduled runs every ~30-36 min on this repo, so each run covers a ~38 min window itself.
# Every scan is a fresh `python scanner.py` process, so grok_budget.json / seen.json are reloaded from disk each time:
#   - max_grok_calls_per_run (3) applies per scan iteration
#   - the daily cap (40/UTC day) stays the true limit across iterations and runs (budget file is committed each scan)
set -uo pipefail
INTERVAL_S=${SCAN_INTERVAL_S:-300}
WINDOW_S=${SCAN_WINDOW_S:-2280}          # 38 min -> scans at 0,5,...,35 min = 8 scans
FILES="seen.json paper_trades.csv scanner_log.csv scanner_runs.log grok_budget.json paper_trades_quick.csv"
T0=$(date +%s)
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

commit_state() {
  git add $FILES
  if git diff --cached --quiet; then echo "no state changes"; return 0; fi
  git commit -q -m "scan state $(date -u +%FT%TZ) [skip ci]"
  for i in 1 2 3 4 5; do
    if git push -q origin HEAD:main; then echo "state pushed"; return 0; fi
    echo "push rejected (attempt $i); rebasing on latest main"
    if ! git pull -q --rebase origin main; then
      git rebase --abort 2>/dev/null || true
      # Conflict on state files: keep this run's state (it is newest), rebuild the commit on top of main
      echo "rebase conflict; re-applying this run's state on top of latest main"
      for f in $FILES; do cp "$f" "/tmp/$f.keep"; done
      git fetch -q origin main && git reset -q --hard origin/main
      for f in $FILES; do cp "/tmp/$f.keep" "$f"; done
      git add $FILES
      git diff --cached --quiet && return 0
      git commit -q -m "scan state $(date -u +%FT%TZ) [skip ci]"
    fi
    sleep $((i * 3))
  done
  echo "state push failed after retries (will retry next iteration)"; return 1
}

if [ "${1:-}" = "--commit-only" ]; then commit_state; exit $?; fi

n=0
while :; do
  start=$(date +%s); n=$((n + 1))
  echo "=== scan iteration $n at $(date -u +%FT%TZ) ==="
  if [ -z "${XAI_API_KEY:-}" ]; then echo "[$(date -u +%FT%TZ)] WARNING XAI_API_KEY secret missing; Grok calls will fail -> SKIP" >> scanner_runs.log; fi
  timeout 360 python scanner.py 2>&1 | tee -a scanner_runs.log || echo "[$(date -u +%FT%TZ)] scanner exited with code $?" >> scanner_runs.log
  tail -n 5000 scanner_runs.log > scanner_runs.log.tmp && mv scanner_runs.log.tmp scanner_runs.log
  commit_state || true
  next=$((start + INTERVAL_S))
  if [ $((next - T0)) -ge "$WINDOW_S" ]; then echo "window done after $n scans"; break; fi
  wait_s=$((next - $(date +%s)))
  [ "$wait_s" -gt 0 ] && { echo "sleeping ${wait_s}s"; sleep "$wait_s"; }
done
