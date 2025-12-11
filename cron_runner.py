# cron_runner.py
import json
from tennis_core import run_all

CACHE_FILE = "data_cache.json"

def refresh():
    print("[Cron] Updating tennis cache...")
    facilities, availability = run_all()

    cache = {
        "facilities": facilities,
        "availability": availability
    }

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)

    print("[Cron] Cache updated successfully. Items:", len(facilities))


if __name__ == "__main__":
    refresh()
