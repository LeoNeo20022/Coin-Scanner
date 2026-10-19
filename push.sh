#!/bin/bash
# One-time publish of the log-only scanner to GitHub. NOT run automatically.
# Usage (from a login shell so XAI_API_KEY is set):
#   GH_TOKEN=... GH_OWNER=<github username> bash -lc /workspace/coin-scanner-gh/push.sh
# The repo https://github.com/$GH_OWNER/coin-scanner must already exist (public, empty).
# Fine-grained token, only that repo: Contents RW, Workflows RW, Secrets RW, Actions RW, Metadata R.
# The token is passed to git through a credential helper that reads it from the environment at run time,
# so it is never written to .git/config, never on a command line, and never printed.
# Set RESEED=1 to copy the latest seen.json / paper_trades.csv / scanner_log.csv from /workspace/copyfilter
# first (stop the box loop first so the two don't diverge).
set -euo pipefail
cd "$(dirname "$0")"
REPO=coin-scanner
: "${GH_TOKEN:?set GH_TOKEN}"; : "${GH_OWNER:?set GH_OWNER}"
[ -n "${XAI_API_KEY:-}" ] || { echo "XAI_API_KEY not set in this shell (use bash -lc)"; exit 1; }
export GH_TOKEN GH_OWNER REPO

if [ "${RESEED:-0}" = "1" ]; then
  cp /workspace/copyfilter/{seen.json,paper_trades.csv,scanner_log.csv} .
  git add seen.json paper_trades.csv scanner_log.csv
  git diff --cached --quiet || git commit -q -m "reseed state from box scanner"
fi

# 1) push main
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$GH_OWNER/$REPO.git"
git -c credential.helper= \
    -c 'credential.helper=!f() { test "$1" = get && echo username=x-access-token && echo "password=$GH_TOKEN"; }; f' \
    push -u origin main
echo "pushed main"

# 2) set the XAI_API_KEY repo secret (sealed box with the repo's public key) and 3) dispatch once
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q requests pynacl
.venv/bin/python - <<'PY'
import base64, os, sys, time, requests
from nacl import encoding, public
tok, owner, repo = os.environ["GH_TOKEN"], os.environ["GH_OWNER"], os.environ["REPO"]
api = f"https://api.github.com/repos/{owner}/{repo}"
H = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
def die(msg, r): sys.exit(f"{msg}: HTTP {r.status_code} {r.text[:300]}")
r = requests.get(f"{api}/actions/secrets/public-key", headers=H, timeout=30)
if r.status_code != 200: die("get public key failed", r)
pk = r.json()
box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
enc = base64.b64encode(box.encrypt(os.environ["XAI_API_KEY"].encode())).decode()
r = requests.put(f"{api}/actions/secrets/XAI_API_KEY", headers=H, timeout=30,
                 json={"encrypted_value": enc, "key_id": pk["key_id"]})
if r.status_code not in (201, 204): die("set secret failed", r)
print("secret XAI_API_KEY set")
for i in range(6):                       # the workflow can take a few seconds to register after the first push
    r = requests.post(f"{api}/actions/workflows/scan.yml/dispatches", headers=H, timeout=30, json={"ref": "main"})
    if r.status_code == 204: print("workflow_dispatch triggered"); break
    time.sleep(5)
else: die("dispatch failed", r)
print(f"runs: https://github.com/{owner}/{repo}/actions")
PY
