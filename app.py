import json, os
from datetime import datetime, timedelta, timezone, date
import requests
from flask import Flask, redirect, request, session, jsonify, render_template

from tennis_core import run_all

# ==========================
# Flask
# ==========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY")
KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET")
KAKAO_REDIRECT_URI = os.environ.get("KAKAO_REDIRECT_URI")

KST = timezone(timedelta(hours=9))

CACHE_FILE = "data_cache.json"
LAST_FILE = "last_slots.json"
ALERT_FILE = "alerts.json"
USERS_FILE = "users.json"


# ==========================
# JSON helpers
# ==========================
def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def today_yyyymmdd_kst():
    return datetime.now(KST).strftime("%Y%m%d")


# ==========================
# Alerts cleanup (지난 날짜 자동 삭제)
# ==========================
def cleanup_expired_alerts(alerts: dict) -> dict:
    """alerts: {uid: [{"group": "...", "date": "YYYYMMDD"} , ...], ...}"""
    today = today_yyyymmdd_kst()
    cleaned = {}
    for uid, items in alerts.items():
        kept = []
        for it in items:
            d = str(it.get("date", "")).replace("-", "")
            if len(d) == 8 and d >= today:
                kept.append({"group": it.get("group", "").strip(), "date": d})
        if kept:
            # 중복 제거
            uniq = {}
            for it in kept:
                uniq[f"{it['group']}|{it['date']}"] = it
            cleaned[uid] = list(uniq.values())
    return cleaned


# ==========================
# Kakao Login
# ==========================
@app.route("/auth/kakao")
def kakao_login():
    return redirect(
        "https://kauth.kakao.com/oauth/authorize"
        "?response_type=code"
        f"&client_id={KAKAO_REST_API_KEY}"
        f"&redirect_uri={KAKAO_REDIRECT_URI}"
    )

@app.route("/auth/kakao/callback")
def kakao_callback():
    code = request.args.get("code")
    if not code:
        return "code 없음", 400

    token_res = requests.post(
        "https://kauth.kakao.com/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "client_id": KAKAO_REST_API_KEY,
            "client_secret": KAKAO_CLIENT_SECRET,
            "redirect_uri": KAKAO_REDIRECT_URI,
            "code": code,
        },
        timeout=20
    ).json()

    access_token = token_res.get("access_token")
    if not access_token:
        return f"카카오 토큰 실패: {token_res}", 400

    user = requests.get(
        "https://kapi.kakao.com/v2/user/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20
    ).json()

    uid = str(user.get("id"))
    nickname = (user.get("properties") or {}).get("nickname", "")

    users = load_json(USERS_FILE, {})
    users[uid] = {
        "nickname": nickname,
        "access_token": access_token
    }
    save_json(USERS_FILE, users)

    session["uid"] = uid
    return redirect("/")

@app.route("/logout")
def logout():
    session.pop("uid", None)
    return redirect("/")


# ==========================
# Kakao send (나에게 보내기)
# ==========================
def send_kakao(token, text):
    # 실패해도 서버 죽지 않게
    try:
        requests.post(
            "https://kapi.kakao.com/v2/api/talk/memo/default/send",
            headers={"Authorization": f"Bearer {token}"},
            data={"template_object": json.dumps({
                "object_type": "text",
                "text": text,
                "link": {"web_url": request.host_url.rstrip("/")}
            })},
            timeout=20
        )
    except Exception as e:
        print("[KAKAO SEND ERROR]", e)


# ==========================
# Cache + Detect + Notify
# ==========================
def refresh_cache():
    facilities, availability = run_all()

    cache = {
        "facilities": facilities,
        "availability": availability,
        "updated_at": datetime.now(KST).isoformat()
    }
    save_json(CACHE_FILE, cache)

    # 알림/슬롯 검사
    detect_and_notify(facilities, availability)


def detect_and_notify(facilities, availability):
    # 지난 날짜 알림 자동 삭제
    alerts = load_json(ALERT_FILE, {})
    alerts = cleanup_expired_alerts(alerts)
    save_json(ALERT_FILE, alerts)

    last = load_json(LAST_FILE, {})
    users = load_json(USERS_FILE, {})

    current = {}

    # 새로 생긴 슬롯만 잡아서 알림 처리
    for rid, days in availability.items():
        for d, slots in days.items():
            for s in slots:
                time_txt = s.get("timeContent", "")
                key = f"{rid}|{d}|{time_txt}"
                current[key] = True

                # 새 슬롯이면
                if key not in last:
                    title = (facilities.get(rid) or {}).get("title", "")
                    # 등록된 알림 매칭
                    for uid, conds in list(alerts.items()):
                        if uid not in users:
                            continue
                        token = users[uid].get("access_token")
                        if not token:
                            continue

                        # uid의 알림들 중 매칭되면 1회 알림 후 해당 알림 제거
                        remaining = []
                        for c in conds:
                            if c.get("date") == d and c.get("group") and c["group"] in title:
                                msg = (
                                    f"📢 테니스 예약 알림\n"
                                    f"{title}\n"
                                    f"{d}  {time_txt}\n"
                                    f"예약 가능합니다."
                                )
                                send_kakao(token, msg)
                                # 이 알림은 1회 발송 후 제거
                            else:
                                remaining.append(c)

                        if remaining:
                            alerts[uid] = remaining
                        else:
                            alerts.pop(uid, None)

    save_json(LAST_FILE, current)
    save_json(ALERT_FILE, alerts)


# ==========================
# UI Routes / APIs
# ==========================
@app.route("/")
def index():
    return render_template("ios_template.html")

@app.route("/me")
def me():
    uid = session.get("uid")
    if not uid:
        return jsonify({"logged_in": False})

    users = load_json(USERS_FILE, {})
    return jsonify({
        "logged_in": True,
        "uid": uid,
        "nickname": (users.get(uid) or {}).get("nickname", "")
    })

@app.route("/data")
def data():
    if not os.path.exists(CACHE_FILE):
        refresh_cache()

    cache = load_json(CACHE_FILE, {})
    if "updated_at" not in cache:
        cache["updated_at"] = datetime.now(KST).isoformat()
    return jsonify(cache)

@app.route("/refresh")
def refresh():
    refresh_cache()
    return jsonify({"status": "ok", "updated_at": datetime.now(KST).isoformat()})

@app.route("/alerts")
def alerts_list():
    uid = session.get("uid")
    if not uid:
        return jsonify({"logged_in": False, "alerts": []})

    alerts = load_json(ALERT_FILE, {})
    alerts = cleanup_expired_alerts(alerts)
    save_json(ALERT_FILE, alerts)

    return jsonify({"logged_in": True, "alerts": alerts.get(uid, [])})

@app.route("/alert/register", methods=["POST"])
def alert_register():
    uid = session.get("uid")
    if not uid:
        return jsonify({"error": "login required"}), 401

    body = request.json or {}
    group = (body.get("group") or "").strip()
    d = str(body.get("date") or "").replace("-", "")  # YYYYMMDD

    # 오늘은 제외(예약 못하니까) → 내일부터만 허용
    today = today_yyyymmdd_kst()
    if len(d) != 8 or d <= today:
        return jsonify({"error": "date must be after today"}), 400

    if not group:
        return jsonify({"error": "group required"}), 400

    alerts = load_json(ALERT_FILE, {})
    alerts = cleanup_expired_alerts(alerts)

    alerts.setdefault(uid, [])
    alerts[uid].append({"group": group, "date": d})

    # 중복 제거
    uniq = {}
    for it in alerts[uid]:
        uniq[f"{it['group']}|{it['date']}"] = it
    alerts[uid] = list(uniq.values())

    save_json(ALERT_FILE, alerts)
    return jsonify({"status": "ok"})

@app.route("/alert/delete", methods=["POST"])
def alert_delete():
    uid = session.get("uid")
    if not uid:
        return jsonify({"error": "login required"}), 401

    body = request.json or {}
    group = (body.get("group") or "").strip()
    d = str(body.get("date") or "").replace("-", "")

    alerts = load_json(ALERT_FILE, {})
    alerts = cleanup_expired_alerts(alerts)

    items = alerts.get(uid, [])
    items = [it for it in items if not (it.get("group") == group and it.get("date") == d)]
    if items:
        alerts[uid] = items
    else:
        alerts.pop(uid, None)

    save_json(ALERT_FILE, alerts)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
