from flask import Flask, jsonify, request, send_file, redirect, session
from datetime import datetime,timezone,timedelta
from collections import defaultdict
import os, json, traceback, requests
import threading
import time
import queue
from pywebpush import webpush
import json

from tennis_core import run_all
from alarm_store import load_alarms, save_alarms, cleanup_old_alarms



# =========================
# Flask 기본 설정
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret")

# =========================
# 환경변수 설정
# =========================
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
KST = timezone(timedelta(hours=9))

# =========================
# 초기 JSON 파일 생성
# =========================
def ensure_json_file(path, default):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)

ensure_json_file("last_slots.json", {})
ensure_json_file("alarm_baseline.json", {})
ensure_json_file("alarms.json", [])
ensure_json_file("users.json", {})


# =========================
# 서비스워커 제공
# =========================

@app.route("/sw.js")
def service_worker():
    return app.send_static_file("sw.js")

# =========================
# 전역 캐시
# =========================
CACHE = {
    "facilities": {},
    "availability": {},
    "updated_at": None
}

# =========================
# 메인 페이지
# =========================
@app.route("/")
def index():
    return send_file("ios_template.html")

# =========================
# 데이터 API
# =========================
@app.route("/data")
def data():
    if not CACHE["updated_at"]:
        try:
            facilities, raw_availability = run_all()
            availability = {}
            for cid, days in raw_availability.items():
                availability[cid] = {}
                for date, slots in days.items():
                    availability[cid][date] = []
                    for s in slots:
                        availability[cid][date].append({
                            "timeContent": s.get("timeContent"),
                            "resveId": s.get("resveId")   # 🔥 이 줄이 핵심
                        })

            CACHE["facilities"] = facilities
            CACHE["availability"] = availability
            CACHE["updated_at"] = datetime.now(KST).isoformat()

        except Exception:
            pass

    return jsonify({
        "facilities": CACHE["facilities"],
        "availability": CACHE["availability"],
        "updated_at": CACHE["updated_at"]

    })

# =========================
# 크롤링 갱신 (UptimeRobot)
# =========================
@app.route("/refresh")
def refresh():
    print("[INFO] refresh start")

    try:
        facilities, availability = crawl_all()
    except Exception as e:
        print("[ERROR] crawl failed", e)
        return "crawl failed", 500

    try:
        new_availability = {}
        for cid, days in availability.items():
            new_availability[cid] = {}
            for date, slots in days.items():
                new_availability[cid][date] = []
                for s in slots:
                    new_availability[cid][date].append({
                    "timeContent": s.get("timeContent"),
                    "resveId": s.get("resveId"),
                    })
        CACHE["facilities"] = facilities
        CACHE["availability"] = new_availability
        CACHE["updated_at"] = datetime.now(KST).isoformat()
        print("[INFO] CACHE updated in /refresh")
    except Exception as e:
        print("[ERROR] cache update failed", e)
        
    try:
        new_slots = detect_new_slots(facilities, availability)
    except Exception as e:
        print("[ERROR] detect failed", e)
        new_slots = []

    try:
        subs = safe_load(PUSH_SUB_FILE, [])
        alarms = safe_load("alarms.json", [])

        for slot in new_slots:
            for alarm in alarms:
                if not match_alarm_condition(alarm, slot):
                    continue

                sub = next(
                    (s["subscription"] for s in subs if s["id"] == alarm["subscription_id"]),
                    None
                )
                if not sub:
                    continue

                send_push_notification(
                    sub,
                    "🎾 예약 가능!",
                    f"{slot['court_title']}\n{slot['date']} {slot['time']}"
                )
    except Exception as e:
        print("[ERROR] push notification failed", e)
        traceback.print_exc()

    print(f"[INFO] refresh done (new={len(new_slots)})")
    return "ok"


# =========================
# 알람 API (사용자별)
# =========================
@app.route("/alarm/list")
def alarm_list():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify([])

    alarms = safe_load("alarms.json", [])
    if not isinstance(alarms, list):
       alarms = []

    return jsonify([a for a in alarms if a.get("user_id") == user_id])

@app.route("/alarm/add", methods=["POST"])
def alarm_add():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "login required"}), 401

    body = request.json
    alarms = safe_load("alarms.json", [])
    if not isinstance(alarms, list):
       alarms = []

    for a in alarms:
        if (
            a["user_id"] == user_id and
            a["court_group"] == body["court_group"] and
            a["date"] == body["date"]
        ):
            return jsonify({"error": "duplicate"}), 409
    
    alarms.append({
        "user_id": user_id,
        "court_group": body.get("court_group"),
        "date": body.get("date"),
        "created_at": datetime.now(KST).isoformat()
    })
    safe_save("alarms.json",alarms)
    save_alarm_baseline(user_id)

    return jsonify({"status": "ok"})

# =========================
# Push 구독 저장 API
# =========================

PUSH_SUB_FILE = "push_subscriptions.json"
ensure_json_file(PUSH_SUB_FILE, [])

import hashlib

def make_subscription_id(sub):
    raw = json.dumps(sub, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()

@app.route("/push/subscribe", methods=["POST"])
def push_subscribe():
    sub = request.json
    subs = safe_load(PUSH_SUB_FILE, [])

    sid = make_subscription_id(sub)

    if not any(s["id"] == sid for s in subs):
        subs.append({
            "id": sid,
            "subscription": sub,
            "created_at": datetime.now(KST).isoformat()
        })
        safe_save(PUSH_SUB_FILE, subs)

    return jsonify({"subscription_id": sid})

# =========================
# 헬스체크
# =========================
@app.route("/health")
def health():
    return "ok"

#==========================
# 알람 삭제
#==========================
@app.route("/alarm/delete", methods=["POST"])
def alarm_delete():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "login required"}), 401

    body = request.json
    court = body.get("court_group")
    date = body.get("date")

    alarms = safe_load("alarms.json", [])
    if not isinstance(alarms, list):
       alarms = []

    alarms = [
        a for a in alarms
        if not (
            a["user_id"] == user_id and
            a["court_group"] == court and
            a["date"] == date
        )
    ]
    safe_save("alarms.json", alarms)

    return jsonify({"status": "ok"})

# =========================
# 안전한 JSON 로드/저장
# =========================

def safe_load(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, type(default)) else default
    except Exception as e:
        print(f"[WARN] JSON load failed: {path} | {e}")
        return default



def safe_save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] JSON save failed: {path} | {e}")

# =========================
# 새 슬롯 감지
# =========================
#         
def detect_new_slots(facilities, availability):
    sent = safe_load("last_slots.json", {})
    if not isinstance(sent, dict):
        sent = {}

    baseline = safe_load("alarm_baseline.json", {})
    if not isinstance(baseline, dict):
        baseline = {}


    new_slots = []

    for cid, days in availability.items():
        title = facilities.get(cid, {}).get("title", "알 수 없음")

        for date, slots in days.items():
            for s in slots:
                key = f"{cid}|{date}|{s['timeContent']}"
                
                # 1️⃣ baseline 차단
                if any(key in user_base for user_base in baseline.values()):
                    continue

                # 2️⃣ 이미 발송된 슬롯 차단
                if sent.get(key):
                    continue

                new_slots.append({
                    "key": key,
                    "cid": cid,
                    "court_title": title,
                    "date": date,
                    "time": s["timeContent"],
                })

                sent[key] = True

    safe_save("last_slots.json", sent)
    return new_slots

# =========================
# 알람 기준 저장
# =========================
def save_alarm_baseline(user_id):
    baseline = safe_load("alarm_baseline.json", {})
    if not isinstance(baseline, dict):
        baseline = {}
    snapshot = {}

    for cid, days in CACHE["availability"].items():
        for date, slots in days.items():
            for s in slots:
                key = f"{cid}|{date}|{s['timeContent']}"
                snapshot[key] = True

    baseline[user_id] = snapshot

    safe_save("alarm_baseline.json", baseline)
# =========================
def crawl_all():
    return run_all() 

# =========================
def group_slots_by_user(new_slots):
    grouped = defaultdict(list)
    for s in new_slots:
        grouped[s["user_id"]].append(s)
    return grouped
# =========================
def make_reserve_link(resve_id):
    base = "https://publicsports.yongin.go.kr/publicsports/sports/selectFcltyRceptResveViewU.do"
    return (
        f"{base}"
        f"?key=4236"
        f"&resveId={resve_id}"
        f"&pageUnit=8"
        f"&pageIndex=1"
        f"&checkSearchMonthNow=false"
    )
# =========================
#  알림 전송
# =========================
def send_push_notification(subscription, title, body):
    payload = json.dumps({
        "title": title,
        "body": body
    })

    webpush(
        subscription_info=subscription,
        data=payload,
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_claims="ccoo2000@naver.com"
    )

# =========================
# 푸시 테스트 (20초 지연)
# =========================
import threading
import time

@app.route("/push/test", methods=["POST"])
def push_test():
    data = request.json
    subscription_id = data.get("subscription_id")

    if not subscription_id:
        return jsonify({"error": "subscription_id missing"}), 400

    subs = safe_load(PUSH_SUB_FILE, [])
    sub = next((s["subscription"] for s in subs if s["id"] == subscription_id), None)

    if not sub:
        return jsonify({"error": "subscription not found"}), 404

    def delayed_push():
        time.sleep(20)
        send_push_notification(
            sub,
            "🔔 Push 테스트",
            "알람 등록 20초 후 테스트 알림입니다."
        )

    threading.Thread(target=delayed_push, daemon=True).start()

    return jsonify({"status": "ok", "message": "20초 후 알림이 전송됩니다"})
# =========================
