import os, json, time, requests, sys
sys.path.insert(0, "."); import socket
from prompts import SCANNER_PROMPT
payload = {"market": {"symbol": "KIBI", "dex": "pumpswap", "mcap_usd": 108980, "liquidity_usd": 29888, "age_hours": 1.8, "volume_h1_usd": 169976, "volume_h6_usd": 700000, "buys_h1": 2227, "sells_h1": 1692, "buys_m5": 200, "sells_m5": 180, "price_change_m5_pct": -3, "price_change_h1_pct": 71, "price_change_h6_pct": 143, "liquidity_to_mcap": 0.274},
           "token": {"source": "fallback:goplus+jupiter", "risk_score": None, "mint_authority": None, "freeze_authority": None, "transfer_restrictions": [], "warn": [], "top10_pct": 17.6, "max_holder_pct": None, "lp_locked_pct": 100.0, "insider_nets": None, "holder_count": 1182, "organic_score": 65.7, "not_available": ["risk_score", "insider_nets", "max_holder_pct"]}}
model = sys.argv[1]
if len(sys.argv) > 2 and sys.argv[2] == "v4":
    import urllib3.util.connection as uc; uc.allowed_gai_family = lambda: socket.AF_INET
t = time.time()
r = requests.post("https://api.x.ai/v1/chat/completions", headers={"Authorization": f"Bearer {os.environ['XAI_API_KEY']}"},
    json={"model": model, "temperature": 0, "messages": [{"role": "system", "content": SCANNER_PROMPT}, {"role": "user", "content": json.dumps(payload)}]}, timeout=(10, 200))
d = r.json()
print(model, r.status_code, f"{time.time()-t:.1f}s", "served_by:", d.get("model"), "usage:", json.dumps(d.get("usage"))[:400], "| reply:", (d.get("choices") or [{}])[0].get("message", {}).get("content", "")[:120] if r.ok else str(d)[:200])
