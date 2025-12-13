import json, os
from datetime import datetime, timedelta, timezone
from tennis_core import run_all
import requests
from flask import Flask, redirect, request, session, jsonify, render_template

# --------------------------
# Flask 앱 (단 한 번만 선언!)
# --------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY")
KAKAO_REDIRECT_URI = os.environ.get("KAKAO_REDIRECT_URI")

# ==========================
# 카카오 로그인
# ==========================
@app.route("/auth/kakao")
def kakao_login():
    kakao_auth_url = (
        "https://kauth.kakao.com/oauth/authorize"
        "?response_type=code"
        f"&client_id={KAKAO_REST_API_KEY}"
        f"&redirect_uri={KAKAO_REDIRECT_URI}"
    )
    return redirect(kakao_auth_url)


@app.route("/auth/kakao/callback")
def kakao_callback():
    code = request.args.get("code")

    token_res = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": KAKAO_REST_API_KEY,
            "redirect_uri": KAKAO_REDIRECT_URI,
            "code": code,
        }
    ).json()

    access_token = token_res.get("access_token")
    if not access_token:
        return "카카오 토큰 발급 실패", 400

    user_res = requests.get(
        "https://kapi.kakao.com/v2/user/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    session["kakao_user"] = user_res
    return redirect("/login-success")


@app.route("/login-success")
def login_success():
    user = session.get("kakao_user")
    return jsonify({
        "status": "success",
        "user_id": user.get("id"),
        "nickname": user.get("properties", {}).get("nickname")
    })


# ==========================
# 캐시 / 데이터
# ==========================
KST = timezone(timedelta(hours=9))
CACHE_FILE = os.path.join(os.path.dirname(__file__), "data_cache.json")


def refresh_cache():
    print("[Cache] Refresh started")
    facilities, availability = run_all()

    cache = {
        "facilities": facilities,
        "availability": availability,
        "updated_at": datetime.now(KST).isoformat()
    }

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print("[Cache] Refresh completed:", cache["updated_at"])


@app.route("/")
def index():
    return render_template("ios_template.html")


@app.route("/data")
def get_data():
    if not os.path.exists(CACHE_FILE):
        refresh_cache()

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)

    return jsonify(cache)


@app.route("/refresh")
def refresh():
    refresh_cache()
    return jsonify({
        "status": "success",
        "updated_at": datetime.now(KST).isoformat()
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
