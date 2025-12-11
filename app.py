from flask import Flask, jsonify, render_template
import threading
import json
import os
import time
from datetime import datetime
from tennis_core import run_all

app = Flask(__name__)



# --------------------------
# 캐시 생성 / 갱신
# --------------------------
CACHE_FILE = os.path.join(os.path.dirname(__file__), "data_cache.json")

def refresh_cache():
    print("[Cache] Refresh started")

    try:
        facilities, availability = run_all()

        cache = {
            "facilities": facilities,
            "availability": availability,
            "updated_at": datetime.now().isoformat()
        }

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        print("[Cache] Refresh completed:", cache["updated_at"])

    except Exception as e:
        print("[Cache] ERROR:", e)

# --------------------------
# 백그라운드 스레드
# --------------------------
def background_worker():
    while True:
        refresh_cache()
        time.sleep(600)   # 10분(600초)


# --------------------------
# 웹 라우트
# --------------------------
@app.route("/")
def index():
    return render_template("ios_template.html")


@app.route("/data")
def get_data():
    if not os.path.exists(CACHE_FILE):
        print("[Cache] Missing file → generating new one")
        refresh_cache()

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception as e:
        print("[Cache] Corrupt file → regenerating:", e)
        refresh_cache()
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

    return jsonify(cache)


@app.route("/refresh")
def refresh():
    try:
        refresh_cache()
        return jsonify({
            "status": "success",
            "updated_at": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# --------------------------
# 앱 시작 시 백그라운드 스레드 실행
# --------------------------
if __name__ == "__main__":
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=8000)
