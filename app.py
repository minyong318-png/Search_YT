import json, os
from datetime import datetime, timedelta, timezone
from tennis_core import run_all
import requests
from flask import Flask, redirect, request, session, jsonify, render_template

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY")
KAKAO_REDIRECT_URI = os.environ.get("KAKAO_REDIRECT_URI")

# 1️⃣ 카카오 로그인 시작
@app.route("/auth/kakao")
def kakao_login():
    kakao_auth_url = (
        "https://kauth.kakao.com/oauth/authorize"
        "?response_type=code"
        f"&client_id={KAKAO_REST_API_KEY}"
        f"&redirect_uri={KAKAO_REDIRECT_URI}"
    )
    return redirect(kakao_auth_url)

# 2️⃣ 카카오 콜백
@app.route("/auth/kakao/callback")
def kakao_callback():
    code = request.args.get("code")

    token_url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": KAKAO_REST_API_KEY,
        "redirect_uri": KAKAO_REDIRECT_URI,
        "code": code,
    }

    token_res = requests.post(token_url, data=data).json()
    access_token = token_res.get("access_token")

    if not access_token:
        return "카카오 토큰 발급 실패", 400

    # 사용자 정보 요청
    user_res = requests.get(
        "https://kapi.kakao.com/v2/user/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    session["kakao_user"] = user_res

    return redirect("/login-success")

# 3️⃣ 로그인 성공 확인용
@app.route("/login-success")
def login_success():
    user = session.get("kakao_user")
    return jsonify({
        "status": "success",
        "user_id": user.get("id"),
        "nickname": user.get("properties", {}).get("nickname")
    })

app = Flask(__name__)

KST = timezone(timedelta(hours=9))
CACHE_FILE = os.path.join(os.path.dirname(__file__), "data_cache.json")


# --------------------------
# 캐시 파일 생성 함수
# --------------------------
def refresh_cache():
    print("[Cache] Refresh started")

    try:
        facilities, availability = run_all()

        cache = {
            "facilities": facilities,
            "availability": availability,
            "updated_at": datetime.now(KST).isoformat()
        }

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        print("[Cache] Refresh completed:", cache["updated_at"])

    except Exception as e:
        print("[Cache] ERROR:", e)


# --------------------------
# HTML 페이지
# --------------------------
@app.route("/")
def index():
    return render_template("ios_template.html")


# --------------------------
# /data API (항상 최신 캐시 반환)
# --------------------------
@app.route("/data")
def get_data():
    # 캐시 없으면 생성
    if not os.path.exists(CACHE_FILE):
        refresh_cache()

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception as e:
        print("[Cache] JSON 오류 → 캐시 재생성:", e)
        refresh_cache()
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

    if "updated_at" not in cache:
        cache["updated_at"] = datetime.now(KST).isoformat()

    return jsonify(cache)


# --------------------------
# /refresh API (외부에서 강제 캐시 갱신)
# --------------------------
@app.route("/refresh")
def refresh():
    try:
        refresh_cache()
        return jsonify({
            "status": "success",
            "updated_at": datetime.now(KST).isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# --------------------------
# Flask 실행
# --------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
