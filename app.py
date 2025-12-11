from flask import Flask, Response
import asyncio
import os
from datetime import datetime
from tennis_core import run_all

app = Flask(__name__)

TEMPLATE_PATH = os.path.join("templates", "ios_template.html")

# 슬롯 변화 추적 변수 → 단순 상태 확인용 (알림 없음)
last_slots = 0


def run_async(coro):
    loop = asyncio.new_event_loop()
    return loop.run_until_complete(coro)


# 날짜 포맷: '1월 12일 (수)'
def format_date(date_str):
    dt = datetime.strptime(date_str, "%Y%m%d")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][dt.weekday()]
    return f"{dt.month}월 {dt.day}일 ({weekday})"


@app.route("/tennis")
def tennis_page():
    facilities, availability = run_all()

    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()

    content = ""
    for rid, info in facilities.items():
        title = info["title"]
        location = info["location"]
        dates = availability.get(rid, {})

        card = f"""
        <div class="court-card">
            <div class="court-title">{title}</div>
            <div class="court-location">{location}</div>
        """

        for date_val, times in sorted(dates.items()):
            display_date = format_date(date_val)
            card += f"""
            <div class="date-section">
                <div class="date-header">{display_date}</div>
            """

            for t in times:
                card += f'<div class="time-slot">{t.get("timeContent")}</div>'

            card += "</div>"

        card += "</div>"
        content += card

    html = template.replace("<!-- Python이 채움 -->", content)
    return Response(html, mimetype="text/html")


@app.route("/check")
def check_slots():
    """
    이제 /check 는 단순히 전체 slot 개수만 반환하는 상태 체크 API
    알림 기능 없음
    """
    global last_slots

    facilities, availability = run_async(run_all())

    current = sum(
        len(times)
        for days in availability.values()
        for times in days.values()
    )

    last_slots = current  # 알림 없이 값만 갱신
    return {"status": "ok", "slots": current}
