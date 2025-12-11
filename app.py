from flask import Flask, Response
import asyncio
import os
from datetime import datetime
from tennis_core import run_all
import requests

app = Flask(__name__)

TEMPLATE_PATH = os.path.join("templates", "ios_template.html")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

last_slots = 0


def run_async(coro):
    loop = asyncio.new_event_loop()
    return loop.run_until_complete(coro)


# 날짜 포맷 함수 (년도 제거 + 요일 추가)
def format_date(date_str):
    dt = datetime.strptime(date_str, "%Y%m%d")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][dt.weekday()]
    return f"{dt.month}월 {dt.day}일 ({weekday})"


@app.route("/tennis")
def tennis_page():
    facilities, availability = run_async(run_all())

    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()

    content = ""
    for rid, info in facilities.items():
        title = info["title"]
        location = info["location"]
        dates = availability.get(rid, {})

        card = f"""
        <div class="court-card" data-title="{title}" data-location="{location}">
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
    global last_slots

    facilities, availability = run_async(run_all())

    current = sum(
        len(times)
        for days in availability.values()
        for times in days.values()
    )

    if current > last_slots and TELEGRAM_TOKEN:
        msg = f"[용인] 예약 가능 증가! 현재 {current}개"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

    last_slots = current
    return {"status": "ok", "slots": current}
