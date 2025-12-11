from flask import Flask, jsonify, render_template
import threading
import json
import os
import time
from tennis_core import run_all

app = Flask(__name__)

CACHE_FILE = "data_cache.json"


# --------------------------
# 캐시 생성 / 갱신
# --------------------------
def refresh_cache():
    print("[Cache] Refresh started")
    facilities, availability = run_all()

    cache = {
        "facilities": facilities,
        "availability": availability,
        "update_at" : datetime.now().isoformat()
    }

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)

    print("[Cache] Refresh completed. Facilities:", len(facilities))


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
        refresh_cache()

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)

    return jsonify(cache)


# --------------------------
# 앱 시작 시 백그라운드 스레드 실행
# --------------------------
if __name__ == "__main__":
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=8000)
