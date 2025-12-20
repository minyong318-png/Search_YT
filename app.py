from flask import Flask, jsonify, request, send_file, redirect, session, send_from_directory
from datetime import datetime,timezone,timedelta
from collections import defaultdict
import os, json, traceback, requests
import threading
import time
import queue
from pywebpush import webpush
import json
import psycopg2
from psycopg2.extras import RealDictCursor

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
DATABASE_URL = os.environ.get("DATABASE_URL")
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
# 데이터베이스 연결
# =========================
def get_db():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        sslmode="require"
    )

# =========================
# 데이터베이스 초기화
# =========================
def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            # alarms 테이블
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alarms (
                    id SERIAL PRIMARY KEY,
                    subscription_id TEXT NOT NULL,
                    court_group TEXT NOT NULL,
                    date TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE (subscription_id, court_group, date)
                );
            """)

            # 🔥 push_subscriptions 테이블
            cur.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

init_db()
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
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM alarms")
                alarms = cur.fetchall()

                cur.execute("SELECT * FROM push_subscriptions")
                subs = cur.fetchall()

        for slot in new_slots:
            for alarm in alarms:
                if not match_alarm_condition(alarm, slot):
                    continue

                sub_row = next(
                    (s for s in subs if s["id"] == alarm["subscription_id"]),
                    None
                )

                if not sub_row:
                    continue  # 구독 정보 없으면 skip

                sub = {
                    "endpoint": sub_row["endpoint"],
                    "keys": {
                        "p256dh": sub_row["p256dh"],
                        "auth": sub_row["auth"]
                    }
                }

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
@app.route("/push/subscribe", methods=["POST"])
def push_subscribe():
    sub = request.json
    if not sub:
        return jsonify({"error": "no subscription"}), 400

    sid = make_subscription_id(sub)

    endpoint = sub.get("endpoint")
    keys = sub.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "invalid subscription"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO push_subscriptions (id, endpoint, p256dh, auth)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id)
                DO UPDATE SET
                  endpoint = EXCLUDED.endpoint,
                  p256dh = EXCLUDED.p256dh,
                  auth = EXCLUDED.auth
            """, (sid, endpoint, p256dh, auth))

    return jsonify({"subscription_id": sid})

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

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO alarms (subscription_id, court_group, date)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (subscription_id, court_group, date)
                    DO NOTHING
                """, (subscription_id, court_group, date))

                if cur.rowcount == 0:
                    return jsonify({"status": "duplicate"})

        return jsonify({"status": "added"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# 알람 목록 조회 API
# =========================
@app.route("/alarm/list")
def alarm_list():
    subscription_id = request.args.get("subscription_id")
    if not subscription_id:
        return jsonify([])

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT court_group, date, created_at
                FROM alarms
                WHERE subscription_id = %s
                ORDER BY created_at DESC
            """, (subscription_id,))
            rows = cur.fetchall()

    return jsonify(rows)

# =========================
# 알람 삭제 API
# =========================
@app.route("/alarm/delete", methods=["POST"])
def alarm_delete():
    body = request.json or {}

    subscription_id = body.get("subscription_id")
    court_group = body.get("court_group")
    date = body.get("date")

    if not subscription_id or not court_group or not date:
        return jsonify({"error": "invalid request"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM alarms
                WHERE subscription_id=%s AND court_group=%s AND date=%s
            """, (subscription_id, court_group, date))

    return jsonify({"status": "deleted"})
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





