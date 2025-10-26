import csv, datetime
HEADER = ["ts_utc","chain","pair","symbol","price_usd","liq_usd","vol_h1_usd","score"]
def _row(p):
    return [
        datetime.datetime.utcnow().isoformat(),
        p.get("chainId"),
        p.get("pairAddress"),
        (p.get("baseToken") or {}).get("symbol"),
        p.get("priceUsd"),
        (p.get("liquidity") or {}).get("usd"),
        (p.get("volume") or {}).get("h1"),
        p.get("score"),
    ]
def log_pair(p, path="signals.csv"):
    exists = False
    try:
        with open(path, "r", encoding="utf-8") as f:
            exists = True
    except FileNotFoundError:
        exists = False
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists: w.writerow(HEADER)
        w.writerow(_row(p))
