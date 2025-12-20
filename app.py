from flask import Flask, jsonify, request, send_file, redirect, session, send_from_directory
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

ALARM_FILE = "alarms.json"
ensure_json_file(ALARM_FILE, [])
# =========================
# 서비스워커 제공
# =========================

@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js")

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
# 알람 조건과 슬롯 매칭
# =========================

def match_alarm_condition(alarm, slot):
    # 날짜 비교 (YYYY-MM-DD ↔ YYYYMMDD)
    alarm_date = alarm.get("date", "").replace("-", "")
    if alarm_date != slot.get("date"):
        return False

    # 코트 그룹 이름 포함 여부
    court_group = alarm.get("court_group", "")
    if court_group and court_group not in slot.get("court_title", ""):
        return False

    return True

# =========================
# 전체 크롤링 실행
# =========================
def crawl_all():
    return run_all() 

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
        vapid_claims={
            "sub": "mailto:ccoo2000@naver.com"
        }
    )

# =========================
# 알람 등록 API (중복 방지 포함)
# =========================


@app.route("/alarm/add", methods=["POST"])
def alarm_add():
    body = request.json or {}

    subscription_id = body.get("subscription_id")
    court_group = body.get("court_group")
    date = body.get("date")

    if not subscription_id or not court_group or not date:
        return jsonify({"error": "invalid request"}), 400

    alarms = safe_load(ALARM_FILE, [])

    # 🔥 중복 알람 체크 (핵심)
    for a in alarms:
        if (
            a.get("subscription_id") == subscription_id and
            a.get("court_group") == court_group and
            a.get("date") == date
        ):
            return jsonify({
                "status": "duplicate",
                "message": "이미 등록된 알람입니다."
            })

    # ✅ 중복이 아니면 저장
    alarms.append({
        "subscription_id": subscription_id,
        "court_group": court_group,
        "date": date,
        "created_at": datetime.now(KST).isoformat()
    })

    safe_save(ALARM_FILE, alarms)

    return jsonify({
        "status": "added"
    })

# =========================
# 알람 목록 조회 API
# =========================
@app.route("/alarm/list", methods=["GET"])
def alarm_list():
    subscription_id = request.args.get("subscription_id")
    if not subscription_id:
        return jsonify([])

    alarms = safe_load(ALARM_FILE, [])

    # ✅ 이 기기에 등록된 알람만 필터
    result = [
        {
            "court_group": a.get("court_group"),
            "date": a.get("date"),
            "created_at": a.get("created_at")
        }
        for a in alarms
        if a.get("subscription_id") == subscription_id
    ]

    return jsonify(result)

# =========================
# 알람 삭제 API
# =========================
@app.route("/alarm/delete", methods=["POST"])
def alarm_delete():
    body = request.json or {}

    subscription_id = body.get("subscription_id")
    court_group = body.get("court_group")
    date = body.get("date")

    if not subscription_id or not date or not court_group:
        return jsonify({"error": "invalid request"}), 400

    alarms = safe_load(ALARM_FILE, [])

    before = len(alarms)

    # ✅ 이 기기 + 같은 조건 알람만 제거
    alarms = [
        a for a in alarms
        if not (
            a.get("subscription_id") == subscription_id
            and a.get("date") == date
            and a.get("court_group") == court_group
        )
    ]

    save_json(ALARM_FILE, alarms)

    return jsonify({
        "removed": before - len(alarms)
    })

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
