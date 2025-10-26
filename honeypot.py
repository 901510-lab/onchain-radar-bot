import os, httpx, logging
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
HONEYPOT_BASE = "https://api.honeypot.is/v2/IsHoneypot"
BIRDEYE_BASE = "https://public-api.birdeye.so"
logger = logging.getLogger("honeypot")

async def check_bsc(token_addr: str) -> dict:
    try:
        url = f"{HONEYPOT_BASE}?address={token_addr}"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url); r.raise_for_status()
            data = r.json()
            is_honey = bool(data.get("isHoneypot"))
            sell_tax = float(data.get("sellTax", 0) or 0)
            if is_honey or sell_tax > 10.0:
                return {"ok": False, "reason": f"honeypot/tax:{sell_tax}"}
            return {"ok": True}
    except Exception as e:
        logger.debug("honeypot bsc error: %s", e)
        return {"ok": True, "reason": "honeypot_api_unavailable"}

async def check_solana(token_addr: str) -> dict:
    try:
        headers = {"accept": "application/json"}
        if BIRDEYE_API_KEY:
            headers["X-API-KEY"] = BIRDEYE_API_KEY
        url = f"{BIRDEYE_BASE}/defi/token_overview?address={token_addr}"
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            r = await client.get(url)
            if r.status_code >= 400:
                return {"ok": True, "reason": f"birdeye_{r.status_code}"}
            js = r.json()
            data = js.get("data", {}) if isinstance(js, dict) else {}
            is_mintable = bool(data.get("mintAuthority", False))
            if is_mintable:
                return {"ok": False, "reason": "mint_authority_enabled"}
            return {"ok": True}
    except Exception as e:
        logger.debug("birdeye sol error: %s", e)
        return {"ok": True, "reason": "birdeye_api_unavailable"}

async def check_token_safety(chain: str, token_addr: str) -> dict:
    c = (chain or "").lower()
    if c in ("bsc","binance","bnb"): return await check_bsc(token_addr)
    if c in ("sol","solana"): return await check_solana(token_addr)
    return {"ok": True, "reason": "unknown_chain"}
