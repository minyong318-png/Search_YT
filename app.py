from flask import Flask, jsonify, request, send_file, redirect, session
from datetime import datetime,timezone,timedelta
import os, json, traceback, requests
import threading
import time

from tennis_core import run_all
from alarm_store import load_alarms, save_alarms, cleanup_old_alarms

# =========================
# Flask 기본 설정
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret")

# =========================
# 카카오 설정 (환경변수)
# =========================
KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY")
KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET")
KAKAO_REDIRECT_URI = os.environ.get("KAKAO_REDIRECT_URI")

USERS_FILE = "users.json"
KST = timezone(timedelta(hours=9))
# =========================
# 유저 저장
# =========================
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(data):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
# 카카오 로그인
# =========================
@app.route("/auth/kakao")
def kakao_login():
    url = (
        "https://kauth.kakao.com/oauth/authorize"
        "?response_type=code"
        f"&client_id={KAKAO_REST_API_KEY}"
        f"&redirect_uri={KAKAO_REDIRECT_URI}"
        "&scope=talk_message"
    )
    return redirect(url)

@app.route("/auth/kakao/callback")
def kakao_callback():
    code = request.args.get("code")

    token = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": KAKAO_REST_API_KEY,
            "client_secret": KAKAO_CLIENT_SECRET,
            "redirect_uri": KAKAO_REDIRECT_URI,
            "code": code,
        }
    ).json()

    access_token = token.get("access_token")
    if not access_token:
        return "카카오 토큰 발급 실패", 400

    user = requests.get(
        "https://kapi.kakao.com/v2/user/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    users = load_users()
    users[str(user["id"])] = {
        "nickname": user["properties"]["nickname"],
        "access_token": access_token,
        "updated_at": datetime.now(KST).isoformat()
    }
    save_users(users)

    session["user_id"] = str(user["id"])
    return redirect("/")

# =========================
# 데이터 API
# =========================
@app.route("/data")
def data():
    if not CACHE["updated_at"]:
        try:
            facilities, availability = run_all()
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
    try:
        facilities, availability = run_all()
        CACHE["facilities"] = facilities
        CACHE["availability"] = availability
        CACHE["updated_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

        cleanup_old_alarms()

        new_slots = detect_new_slots(facilities, availability)
        if new_slots:
            trigger_kakao_alerts(new_slots)

        return jsonify({
            "status": "ok",
            "updated_at": CACHE["updated_at"],
            "new_slots": len(new_slots)
        })
    except Exception:
        traceback.print_exc()
        return jsonify({"status": "error"}), 500

# =========================
# 알람 API (사용자별)
# =========================
@app.route("/alarm/list")
def alarm_list():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify([])

    alarms = load_alarms()
    return jsonify([a for a in alarms if a.get("user_id") == user_id])

@app.route("/alarm/add", methods=["POST"])
def alarm_add():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "login required"}), 401

    body = request.json
    alarms = load_alarms()
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
    save_alarms(alarms)
    save_alarm_baseline(user_id)

    return jsonify({"status": "ok"})

# =========================
# 헬스체크
# =========================
@app.route("/health")
def health():
    return "ok"

#==========================
# 내 정보
#=========================
@app.route("/me")
def me():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"logged_in": False})

    users = load_users()
    user = users.get(user_id)

    # 🔥 users.json에 정보 없으면 로그아웃 처리
    if not user:
        session.clear()
        return jsonify({"logged_in": False})

    return jsonify({
        "logged_in": True,
        "nickname": user.get("nickname", "")
    })

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

    alarms = load_alarms()
    alarms = [
        a for a in alarms
        if not (
            a["user_id"] == user_id and
            a["court_group"] == court and
            a["date"] == date
        )
    ]
    save_alarms(alarms)

    return jsonify({"status": "ok"})
#==========================
# 카카오 테스트 메시지
#==========================
@app.route("/test/kakao")
def test_kakao():
    user_id = session.get("user_id")
    if not user_id:
        return "로그인 필요", 401

    users = load_users()
    user = users.get(user_id)
    if not user:
        return "유저 정보 없음", 400

    access_token = user["access_token"]

    res = send_kakao_message(
        access_token,
        "🔥 카카오 즉시 발송 테스트 메시지"
    )

    return {
        "status": res.status_code,
        "body": res.text
    }



#==========================
# 카카오 메시지 전송 함수  
#==========================
def send_kakao_message(access_token, text):
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "template_object": json.dumps({
            "object_type": "text",
            "text": text,
            "link": {
                "web_url": "https://web-production-e5054.up.railway.app",
                "mobile_web_url": "https://web-production-e5054.up.railway.app"
            }
        })
    }
    return requests.post(url, headers=headers, data=data)

def detect_new_slots(facilities, availability):
    import json, os

    def safe_load(path):
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    # 이전 발송 기록
    sent = safe_load("last_slots.json")

    # 알람 기준선
    baseline = safe_load("alarm_baseline.json")

    new_slots = []

    for cid, days in availability.items():
        title = facilities.get(cid, {}).get("title", "")

        for date, slots in days.items():
            for s in slots:
                key = f"{cid}|{date}|{s['timeContent']}"

                # 1️⃣ baseline에 있으면 무시
                if any(
                    isinstance(user_base, dict) and key in user_base
                    for user_base in baseline.values()
                ):
                    continue

                # 2️⃣ 이미 알림 보냈으면 무시
                if key in sent:
                    continue

                # 3️⃣ 새 슬롯
                new_slots.append({
                    "key": key,
                    "court_title": title,
                    "date": date,
                    "time": s["timeContent"]
                })

                # sent는 여기서만 기록
                sent[key] = True

    # sent 저장 (항상 JSON 보장)
    with open("last_slots.json", "w", encoding="utf-8") as f:
        json.dump(sent, f, ensure_ascii=False, indent=2)

    return new_slots



def load_users():
    if not os.path.exists("users.json"):
        return {}
    with open("users.json", "r", encoding="utf-8") as f:
        return json.load(f)

def trigger_kakao_alerts(new_slots):
    users = load_users()
    alarms = load_alarms()

    for slot in new_slots:
        for alarm in alarms:
            # 코트 그룹 매칭 (부분 포함)
            if alarm["court_group"] not in slot["court_title"]:
                continue

            # 날짜 매칭 (YYYYMMDD ↔ YYYY-MM-DD)
            slot_date = slot["date"]
            alarm_date = alarm["date"].replace("-", "")
            if slot_date != alarm_date:
                continue

            user_id = alarm["user_id"]
            user = users.get(user_id)
            if not user:
                continue

            msg = (
                "🎾 테니스 예약 알림\n\n"
                f"{slot['court_title']}\n"
                f"{slot_date[4:6]}.{slot_date[6:8]} "
                f"{slot['time']}\n\n"
                "지금 예약 가능합니다!"
            )

            send_kakao_message(user["access_token"], msg)
# =========================
# 알람 기준 저장
# =========================
def save_alarm_baseline(user_id):
    import json, os

    baseline = {}
    if os.path.exists("alarm_baseline.json"):
        with open("alarm_baseline.json", "r", encoding="utf-8") as f:
            baseline = json.load(f)

    snapshot = {}
    for cid, days in CACHE["availability"].items():
        for date, slots in days.items():
            for s in slots:
                key = f"{cid}|{date}|{s['timeContent']}"
                snapshot[key] = True

    baseline[user_id] = snapshot

    with open("alarm_baseline.json", "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
