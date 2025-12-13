import json
import uuid
from datetime import datetime

ALARM_FILE = "alarms.json"

def load_alarms():
    try:
        with open(ALARM_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_alarms(data):
    with open(ALARM_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_alarm(user_id, court, date):
    alarms = load_alarms()
    alarms.append({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "court": court,
        "date": date,
        "created": datetime.now().isoformat()
    })
    save_alarms(alarms)

def cleanup_old_alarms():
    today = datetime.now().strftime("%Y-%m-%d")
    alarms = load_alarms()
    alarms = [a for a in alarms if a["date"] >= today]
    save_alarms(alarms)
    return alarms
