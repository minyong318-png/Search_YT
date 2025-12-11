from flask import Flask, jsonify, render_template
import json
import os
from tennis_core import run_all   # 기존 크롤링 함수

app = Flask(__name__)

CACHE_FILE = "data_cache.json"


# -----------------------
#   1) refresh (크롤링 + 캐싱)
# -----------------------
@app.route("/refresh")
def refresh_data():
    try:
        facilities, availability = run_all()

        cache = {
            "facilities": facilities,
            "availability": availability
        }

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)

        return {"status": "updated", "items": len(facilities)}

    except Exception as e:
        return {"status": "error", "message": str(e)}


# -----------------------
#   2) data (캐시 즉시 반환)
# -----------------------
@app.route("/data")
def get_cached_data():
    # 캐시 없으면 자동 초기 생성
    if not os.path.exists(CACHE_FILE):
        facilities, availability = run_all()
        cache = {"facilities": facilities, "availability": availability}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)

    return jsonify(cache)


# -----------------------
#   3) HTML 페이지
# -----------------------
@app.route("/")
def home():
    return render_template("ios_template.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
