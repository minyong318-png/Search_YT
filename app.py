from flask import Flask, Response
import asyncio
import os
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


@app.route("/tennis")
def tennis_page():
    facilities, availability = run_async(run_all())

    # load template
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()

    # build HTML content
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
            card += f"""
            <div class="date-section">
                <div class="date-header">{date_val}</div>
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

    # notify if new slots appear
    if current > last_slots and TELEGRAM_TOKEN:
        msg = f"[용인] 예약 가능 개수 증가! 현재 {current}개"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

    last_slots = current

    return {"status": "ok", "slots": current}
