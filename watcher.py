import os
WATCHLIST_FILE = os.getenv("WATCHLIST_FILE", "watchlist.jsonl")
def load_watchlist():
    if not os.path.exists(WATCHLIST_FILE): return []
    out=set()
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            t=line.strip()
            if t: out.add(t)
    return list(out)
def add_to_watchlist(token: str):
    token=token.strip()
    if not token: return
    tokens=set(load_watchlist())
    if token in tokens: return
    with open(WATCHLIST_FILE, "a", encoding="utf-8") as f:
        f.write(token + "\n")
