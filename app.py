from flask import Flask, jsonify, request, send_file, redirect, session
from datetime import datetime,timezone,timedelta
from collections import defaultdict
import os, json, traceback, requests
import threading
import time
import queue

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

    users = safe_load("users.json",{})
    users[str(user["id"])] = {
        "nickname": user["properties"]["nickname"],
        "access_token": access_token,
        "updated_at": datetime.now(KST).isoformat()
    }
    safe_save("users.json", users)

    session["user_id"] = str(user["id"])
    return redirect("/")

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
        new_slots = detect_new_slots(facilities, availability)
    except Exception as e:
        print("[ERROR] detect failed", e)
        new_slots = []

    try:
        send_notifications(new_slots)
    except Exception as e:
        print("[ERROR] notify failed", e)

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
    return jsonify([a for a in alarms if a.get("user_id") == user_id])

@app.route("/alarm/add", methods=["POST"])
def alarm_add():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "login required"}), 401

    body = request.json
    alarms = safe_load("alarms.json", [])
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

    users = safe_load("users.json", {})
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

    alarms = safe_load("alarms.json", [])
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
#==========================
# 카카오 테스트 메시지
#==========================
@app.route("/test/kakao")
def test_kakao():
    user_id = session.get("user_id")
    if not user_id:
        return "로그인 필요", 401

    users = safe_load("users.json", {})
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
    try:
        res = requests.post(
            "https://kapi.kakao.com/v2/api/talk/memo/default/send",
            headers={"Authorization": f"Bearer {access_token}"},
            data={
                "template_object": json.dumps({
                    "object_type": "text",
                    "text": text,
                    "link": {
                        "web_url": "https://web-production-e5054.up.railway.app",
                        "mobile_web_url": "https://web-production-e5054.up.railway.app"
                    },
                    "button_title": "예약하러 가기"
                })
            },
            timeout=5
        )

        print("[INFO] kakao send", res.status_code, res.text)
        return res

    except Exception as e:
        print("[ERROR] kakao exception", e)
        return None

# =========================
# 안전한 JSON 로드/저장

def safe_load(path, default=None):
    if default is None:
        default = {}

    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else default
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
        
def detect_new_slots(facilities, availability):
    sent = safe_load("last_slots.json", {})
    baseline = safe_load("alarm_baseline.json", {})

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
# 카카오 알림 발송

def trigger_kakao_alerts(new_slots):
    users = safe_load("users.json", {})
    alarms = safe_load("alarms.json", [])
    
    # 🔹 사용자별로 보낼 슬롯 모으기
    user_messages = defaultdict(list)

    for slot in new_slots:
        for alarm in alarms:

            # 1️⃣ 코트 그룹 매칭
            if alarm["court_group"] not in slot["court_title"]:
                continue

            # 2️⃣ 날짜 매칭 (YYYYMMDD ↔ YYYY-MM-DD)
            slot_date = slot["date"]
            alarm_date = alarm["date"].replace("-", "")
            if slot_date != alarm_date:
                continue

            user_id = alarm["user_id"]
            if user_id not in users:
                continue

            # 🔹 여기서는 "보내지 말고" 모으기만 함
            user_messages[user_id].append(slot)

    # 🔔 여기서 사용자당 1번만 발송
    for user_id, slots in user_messages.items():
        user = users[user_id]
        msg_lines = ["🎾 테니스 예약 알림\n"]
        group = alarm["court_group"]
        for s in slots:
            reserve_url = make_reserve_link(s["cid"])
            msg_lines.append(
                f"• [{group}] {s['court_title']}\n"
                f"  {s['date'][4:6]}.{s['date'][6:8]} {s['time']}"
                "👉 지금 예약 가능합니다!\n"
                f"🔗 예약하러 가기\n{reserve_url}"
            )
        text = "\n".join(msg_lines)
        send_kakao_message(user["access_token"], text)
# =========================
# 알람 기준 저장
# =========================
def save_alarm_baseline(user_id):
    baseline = safe_load("alarm_baseline.json", {})

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
def send_notifications(new_slots):
    if not new_slots:
        return

    alarms = safe_load("alarms.json", [])
    users = safe_load("users.json", {})

    for user_id, user_alarms in alarms.items():
        user = users.get(user_id)
        if not user:
            continue

        access_token = user.get("access_token")
        if not access_token:
            continue

        for slot in new_slots:
            # 🔒 기존 로직 유지: 조건 맞을 때만 발송
            if not match_alarm(user_alarms, slot):
                continue
            reserve_url = make_reserve_link(slot["cid"])
            text = (
                f"🎾 예약 가능 알림\n"
                f"• {slot['court_title']}\n"
                f"  {slot['date'][4:6]}.{slot['date'][6:8]} {slot['time']}"
                "👉 지금 예약 가능합니다!\n"
                f"🔗 예약하러 가기\n{reserve_url}"
            )

            send_kakao_message(access_token, text)
# =========================
def match_alarm(user_alarms, slot):
    """
    user_alarms: 해당 사용자가 등록한 알람 리스트
    slot: detect_new_slots에서 발견한 슬롯(dict)
    """

    for alarm in user_alarms:
        # 1️⃣ 날짜 비교
        if alarm.get("date") != slot.get("date"):
            continue

        # 2️⃣ 코트 그룹 비교
        court_group = alarm.get("court_group", "")
        if court_group and court_group not in slot.get("court_title", ""):
            continue

        # 조건 모두 만족
        return True

    return False
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