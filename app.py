from flask import Flask, jsonify, render_template, request
import json, os
from datetime import datetime, timedelta, timezone
from tennis_core import run_all

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
