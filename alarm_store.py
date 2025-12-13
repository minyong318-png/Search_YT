from datetime import date
import json
import os

FILE = "alarms.json"

def load_alarms():
    if not os.path.exists(FILE):
        return []
    with open(FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_alarms(data):
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def cleanup_old_alarms():
    today = date.today().isoformat()
    alarms = load_alarms()
    alarms = [a for a in alarms if a["date"] >= today]
    save_alarms(alarms)
    return alarms
