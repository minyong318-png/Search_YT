from flask import Flask, jsonify, request, send_file
from datetime import datetime
import traceback

from tennis_core import run_all          # ✅ 그대로 사용
from alarm_store import (
    load_alarms,
    save_alarms,
    cleanup_old_alarms
)

app = Flask(__name__)

# =========================
# 전역 캐시 (메모리)
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
# 데이터 조회 (프론트용)
# =========================
@app.route("/data")
def data():
    return jsonify({
        "facilities": CACHE["facilities"],
        "availability": CACHE["availability"],
        "updated_at": CACHE["updated_at"]
    })

# =========================
# 크롤링 갱신 (UptimeRobot이 호출)
# =========================
@app.route("/refresh")
def refresh():
    try:
        facilities, availability = run_all()

        CACHE["facilities"] = facilities
        CACHE["availability"] = availability
        CACHE["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 지난 날짜 알림 자동 정리
        cleanup_old_alarms()

        return jsonify({
            "status": "ok",
            "updated_at": CACHE["updated_at"],
            "facility_count": len(facilities)
        })

    except Exception as e:
        print("[REFRESH ERROR]")
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# =========================
# 알림 목록 조회
# =========================
@app.route("/alarm/list")
def alarm_list():
    return jsonify(load_alarms())

# =========================
# 알림 추가
# =========================
@app.route("/alarm/add", methods=["POST"])
def alarm_add():
    body = request.json

    if not body or "court" not in body or "date" not in body:
        return jsonify({"error": "invalid payload"}), 400

    alarms = load_alarms()

    alarms.append({
        "court": body["court"],
        "date": body["date"]
    })

    save_alarms(alarms)

    return jsonify({"status": "ok"})

# =========================
# 알림 삭제 (옵션)
# =========================
@app.route("/alarm/delete", methods=["POST"])
def alarm_delete():
    body = request.json
    if not body:
        return jsonify({"error": "invalid payload"}), 400

    court = body.get("court")
    date = body.get("date")

    alarms = load_alarms()
    alarms = [
        a for a in alarms
        if not (a.get("court") == court and a.get("date") == date)
    ]

    save_alarms(alarms)
    return jsonify({"status": "ok"})

# =========================
# 헬스체크 (UptimeRobot용)
# =========================
@app.route("/health")
def health():
    return "ok"

# =========================
# 로컬 실행용
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
