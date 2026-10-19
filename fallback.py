# Fallback token-safety check, used only when RugCheck errors. Free, no API keys. Read-only lookups.
# Sources, in order: GoPlus Solana token_security (authorities, Token-2022 transfer restrictions, holders/LP when indexed),
# Jupiter Tokens V2 search (authorities, top-holder %, launchpad/graduated pool), and as a last resort public Solana RPC
# (getAccountInfo for authorities + Token-2022 extensions, getTokenLargestAccounts for holders; that one is often rate-limited).
# If no source answers, report() raises -> the scanner logs the error and SKIPs (never buys unchecked).
import requests
from config import TOKEN, FALLBACK as F

H = {"Accept": "application/json", "User-Agent": "coin-scanner/1.0 (log-only)"}
CLASSIC = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
T2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
# Owners of AMM vaults that are not pair addresses themselves (Raydium AMM v4 / CPMM authorities).
AMM_AUTHORITIES = {"5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL"}

def goplus(mint):
    r = requests.get(F["goplus_url"], params={"contract_addresses": mint}, headers=H, timeout=20)
    r.raise_for_status(); d = r.json()
    if d.get("code") != 1: raise RuntimeError(f"goplus code {d.get('code')}: {d.get('message')}")
    res = d.get("result") or {}
    if mint not in res: raise RuntimeError("goplus: no result for mint")
    return res[mint]

def jupiter(mint):
    r = requests.get(F["jupiter_url"], params={"query": mint}, headers=H, timeout=20)
    r.raise_for_status()
    for t in r.json() or []:
        if t.get("id") == mint: return t
    raise RuntimeError("jupiter: mint not found")

def rpc(method, params):
    r = requests.post(F["rpc_url"], json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=H, timeout=15)
    r.raise_for_status(); d = r.json()
    if "error" in d: raise RuntimeError(f"rpc {method}: {d['error'].get('message')}")
    return d["result"]

def _status(gp, key):          # GoPlus {"authority": [...], "status": "0"|"1"} -> True/False/None
    v = (gp or {}).get(key)
    if not isinstance(v, dict) or v.get("status") is None: return None
    return str(v["status"]) == "1"

def _rpc_holders(mint, supply_raw, pools):
    """Top holders by owner (non-pool), percent 0-100, via public RPC. None if unavailable."""
    accs = rpc("getTokenLargestAccounts", [mint])["value"]
    infos = rpc("getMultipleAccounts", [[a["address"] for a in accs], {"encoding": "jsonParsed"}])["value"]
    by_owner = {}
    for a, v in zip(accs, infos):
        try: owner = v["data"]["parsed"]["info"]["owner"]
        except Exception: owner = a["address"]
        by_owner[owner] = by_owner.get(owner, 0) + int(a["amount"])
    return [{"owner": o, "pct": 100 * amt / supply_raw, "pool": o in pools} for o, amt in by_owner.items()]

def report(mint, pair=None, dex=None):
    errors, used = [], []
    gp = jup = mint_acc = None
    try: gp = goplus(mint); used.append("goplus")
    except Exception as e: errors.append(f"goplus: {str(e)[:80]}")
    try: jup = jupiter(mint); used.append("jupiter")
    except Exception as e: errors.append(f"jupiter: {str(e)[:80]}")

    # --- mint / freeze authority ---
    mintable, freezable = _status(gp, "mintable"), _status(gp, "freezable")
    audit = (jup or {}).get("audit") or {}
    if mintable is None and audit.get("mintAuthorityDisabled") is not None: mintable = not audit["mintAuthorityDisabled"]
    if freezable is None and audit.get("freezeAuthorityDisabled") is not None: freezable = not audit["freezeAuthorityDisabled"]
    program = (jup or {}).get("tokenProgram")
    need_rpc_mint = mintable is None or freezable is None or (program == T2022 and gp is None) or program is None
    ext_flags = None
    if need_rpc_mint:
        try:
            v = rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])["value"]
            program = v["owner"]; info = v["data"]["parsed"]["info"]; used.append("rpc")
            mint_acc = info
            if mintable is None: mintable = info.get("mintAuthority") is not None
            if freezable is None: freezable = info.get("freezeAuthority") is not None
            ext_flags = []
            for ext in info.get("extensions") or []:
                n, s = ext.get("extension"), ext.get("state") or {}
                if n == "permanentDelegate" and s.get("delegate"): ext_flags.append("permanent_delegate")
                if n == "nonTransferable": ext_flags.append("non_transferable")
                if n == "transferHook" and (s.get("programId") or s.get("authority")): ext_flags.append("transfer_hook")
                if n == "transferFeeConfig":
                    bps = max(int((s.get(k) or {}).get("transferFeeBasisPoints") or 0) for k in ("olderTransferFee", "newerTransferFee"))
                    if bps / 100 > F["max_transfer_fee_pct"]: ext_flags.append(f"transfer_fee_{bps / 100:g}%")
                    if s.get("transferFeeConfigAuthority"): ext_flags.append("transfer_fee_upgradable")
                if n == "defaultAccountState" and s.get("accountState") == "frozen": ext_flags.append("default_frozen")
        except Exception as e: errors.append(f"rpc_mint: {str(e)[:80]}")
    if not used: raise RuntimeError("all fallback sources failed: " + " | ".join(errors))

    # --- transfer restrictions / honeypot-style Token-2022 features ---
    restrictions, warn = [], []
    if gp is not None:
        if str(gp.get("non_transferable")) == "1": restrictions.append("non_transferable")
        if _status(gp, "balance_mutable_authority"): restrictions.append("balance_mutable(permanent_delegate)")
        if gp.get("transfer_hook"): restrictions.append("transfer_hook")
        if _status(gp, "transfer_hook_upgradable"): restrictions.append("transfer_hook_upgradable")
        if gp.get("transfer_fee"): restrictions.append("transfer_fee")   # any Token-2022 fee config = tax risk
        if _status(gp, "transfer_fee_upgradable"): restrictions.append("transfer_fee_upgradable")
        if str(gp.get("default_account_state")) == "2": restrictions.append("default_frozen")
        if _status(gp, "default_account_state_upgradable"): restrictions.append("default_state_upgradable")
        if _status(gp, "closable"): warn.append("mint_closable")
        if _status(gp, "metadata_mutable"): warn.append("metadata_mutable")
        transfer_known = True
    elif ext_flags is not None:
        restrictions += ext_flags; transfer_known = True
    else:
        transfer_known = program == CLASSIC     # classic SPL tokens cannot carry transfer hooks/fees/delegates

    # --- holders ---
    pools = {p for p in (pair, (jup or {}).get("graduatedPool")) if p} | AMM_AUTHORITIES
    pools |= {d.get("id") for d in (gp or {}).get("dex") or [] if d.get("id")}
    holders, holder_src = None, None
    if gp and gp.get("holders"):
        holders = [{"owner": h.get("account"), "pct": 100 * float(h.get("percent") or 0),
                    "pool": h.get("account") in pools or h.get("token_account") in pools or bool(h.get("tag"))}
                   for h in gp["holders"]]
        holder_src = "goplus"
    else:
        try:
            if mint_acc is None:
                mint_acc = rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])["value"]["data"]["parsed"]["info"]
            holders = _rpc_holders(mint, int(mint_acc["supply"]), pools); holder_src = "rpc"
            if "rpc" not in used: used.append("rpc")
        except Exception as e: errors.append(f"rpc_holders: {str(e)[:80]}")
    max_holder = top10 = None
    if holders is not None:
        wallets = sorted((h for h in holders if not h["pool"]), key=lambda h: -h["pct"])
        top10 = round(sum(h["pct"] for h in wallets[:10]), 2)
        max_holder = round(wallets[0]["pct"], 2) if wallets else 0.0
    elif audit.get("topHoldersPercentage") is not None:
        top10 = round(float(audit["topHoldersPercentage"]), 2); holder_src = "jupiter_top_holders"
    dev_pct = audit.get("devBalancePercentage")

    # --- LP lock ---
    lp, lp_basis = None, None
    if jup and jup.get("launchpad") == "pump.fun" and pair and jup.get("graduatedPool") == pair and dex == "pumpswap":
        lp, lp_basis = 100.0, "pump.fun graduation pool (LP burned at migration)"
    else:
        for d in (gp or {}).get("dex") or []:
            if d.get("id") == pair and d.get("burn_percent") is not None:
                lp, lp_basis = float(d["burn_percent"]), "goplus burn_percent"
    return {"mint": mint, "source": "fallback:" + "+".join(used), "risk_score": None,
            "mint_authority": None if mintable is False else ("active" if mintable else "unknown"),
            "freeze_authority": None if freezable is False else ("active" if freezable else "unknown"),
            "token_program": program, "transfer_restrictions": restrictions, "transfer_known": transfer_known,
            "danger": [], "warn": warn, "top10_pct": top10, "max_holder_pct": max_holder, "holder_source": holder_src,
            "dev_balance_pct": dev_pct, "lp_locked_pct": lp, "lp_basis": lp_basis, "insider_nets": None,
            "holder_count": (jup or {}).get("holderCount") or (gp or {}).get("holder_count"),
            "organic_score": (jup or {}).get("organicScore"), "launchpad": (jup or {}).get("launchpad"),
            "not_available": [k for k, v in (("risk_score", None), ("insider_nets", None), ("max_holder_pct", max_holder)) if v is None],
            "source_errors": errors}

def fact_kill(t):
    if t["mint_authority"] == "active" or t["freeze_authority"] == "active": return "authority_open"
    if t["mint_authority"] == "unknown" or t["freeze_authority"] == "unknown": return "authority_unknown"
    if t["transfer_restrictions"]: return "transfer_restricted:" + t["transfer_restrictions"][0]
    if not t["transfer_known"]: return "transfer_unknown"
    if t["top10_pct"] is None: return "top10_unknown"
    if t["top10_pct"] > TOKEN["max_top10_pct"]: return "top10"
    if t["max_holder_pct"] is not None:
        if t["max_holder_pct"] > TOKEN["max_single_holder_pct"]: return "single_holder"
    else:   # no per-wallet list: a stricter top-10 cap so one big wallet can't hide inside it
        if t["dev_balance_pct"] is not None and float(t["dev_balance_pct"]) > TOKEN["max_single_holder_pct"]: return "single_holder(dev)"
        if t["top10_pct"] > F["top10_cap_when_holders_unknown"]: return "top10(holders_unknown)"
    if t["lp_locked_pct"] is None: return "lp_unknown"
    if t["lp_locked_pct"] < TOKEN["min_lp_locked_pct"]: return "lp_unlocked"
    return None
