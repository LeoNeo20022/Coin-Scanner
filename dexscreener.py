# Free public DexScreener API, no key. Rate limits (docs): profiles/boosts 60 req/min, pairs/tokens 300 req/min.
import time, requests
BASE = "https://api.dexscreener.com"
DISCOVERY = ["/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1",
             "/community-takeovers/latest/v1"]
H = {"Accept": "application/json", "User-Agent": "copyfilter-scanner/1.0 (log-only)"}

def _get(path, timeout=20):
    for attempt in range(3):
        r = requests.get(BASE + path, headers=H, timeout=timeout)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()

def discover(sleep_s=1.0):
    """Return ({mint: [sources]}, errors) for Solana tokens from the discovery feeds."""
    found, errors = {}, []
    for path in DISCOVERY:
        try:
            d = _get(path)
            items = d if isinstance(d, list) else [d]
            for it in items:
                if it.get("chainId") == "solana" and it.get("tokenAddress"):
                    found.setdefault(it["tokenAddress"], []).append(path.split("/")[1] + ("/top" if "/top/" in path else ""))
        except Exception as e:
            errors.append(f"{path}: {e}")
        time.sleep(sleep_s)
    return found, errors

def pairs_for(mints, sleep_s=1.0):
    """Return {mint: best pair dict} (highest-liquidity Solana pair where the mint is the base token)."""
    best, mints = {}, list(dict.fromkeys(mints))
    for i in range(0, len(mints), 30):          # endpoint takes up to 30 comma-separated addresses
        chunk = mints[i:i + 30]
        d = _get("/tokens/v1/solana/" + ",".join(chunk)) or []
        for p in d:
            m = (p.get("baseToken") or {}).get("address")
            if p.get("chainId") != "solana" or m not in chunk: continue
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if m not in best or liq > ((best[m].get("liquidity") or {}).get("usd") or 0):
                best[m] = p
        time.sleep(sleep_s)
    return best

def all_pairs_for(mints, sleep_s=1.0):
    """Return {mint: [pairs]} for price updates (so we can follow the exact pair we entered on)."""
    out, mints = {}, list(dict.fromkeys(mints))
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        for p in _get("/tokens/v1/solana/" + ",".join(chunk)) or []:
            m = (p.get("baseToken") or {}).get("address")
            if m in chunk: out.setdefault(m, []).append(p)
        time.sleep(sleep_s)
    return out
