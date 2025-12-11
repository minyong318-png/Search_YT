import aiohttp
import asyncio
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import calendar

BASE_URL = "https://publicsports.yongin.go.kr"


async def fetch(session, url, method="GET", data=None):
    try:
        if method == "POST":
            async with session.post(url, data=data, ssl=False) as resp:
                return await resp.text()
        else:
            async with session.get(url, ssl=False) as resp:
                return await resp.text()
    except:
        return None


async def fetch_facilities(session, key):
    base_url = f"{BASE_URL}/publicsports/sports/selectFcltyRceptResveListU.do"

    page = 1
    facilities = {}

    while True:
        params = {
            "key": key,
            "pageUnit": 8,     # 20 or 50 등 크게 설정
            "pageIndex": page,
            "checkSearchMonthNow": "false"
        }

        async with session.get(base_url, params=params, ssl=False) as resp:
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")

        items = soup.select("li.reserve_box_item")
        if not items:
            break  # 더 이상 페이지 없음 → 종료

        for li in items:
            a = li.select_one("div.btn_wrap a[href*='selectFcltyRceptResveViewU.do']")
            if not a:
                continue

            href = a["href"]
            m = re.search(r"resveId=(\d+)", href)
            if not m:
                continue

            rid = m.group(1)

            title_div = li.select_one("div.reserve_title")
            pos_div = title_div.select_one("div.reserve_position")
            location = pos_div.get_text(strip=True) if pos_div else ""
            if pos_div:
                pos_div.extract()

            title = title_div.get_text(strip=True)

            # 테니스 필터는 상황에 따라 조정 가능
            if "테니스" in title or "코트" in title:
                facilities[rid] = {"title": title, "location": location}

        page += 1

    return facilities



async def fetch_times(session, date_val, resve_id, key):
    url = f"{BASE_URL}/publicsports/sports/selectRegistTimeByChosenDateFcltyRceptResveApply.do"

    data = {"dateVal": date_val, "resveId": resve_id}

    async with session.post(url, data=data, ssl=False) as resp:
        try:
            j = await resp.json()
            return j.get("resveTmList", [])
        except:
            return []


async def fetch_availability(session, resve_id, key):
    today = datetime.today()

    # 이번 달 범위
    start_year = today.year
    start_month = today.month
    start_day = today.day
    last_day_this_month = calendar.monthrange(start_year, start_month)[1]

    # 다음 달 범위
    next_month_date = today.replace(day=1) + timedelta(days=32)
    next_year = next_month_date.year
    next_month = next_month_date.month
    last_day_next_month = calendar.monthrange(next_year, next_month)[1]

    tasks = []

    # 오늘 → 이번 달 마지막날
    for d in range(start_day, last_day_this_month + 1):
        date_val = f"{start_year}{start_month:02d}{d:02d}"
        tasks.append(fetch_times(session, date_val, resve_id, key))

    # 다음 달 1일 → 다음달 마지막날
    for d in range(1, last_day_next_month + 1):
        date_val = f"{next_year}{next_month:02d}{d:02d}"
        tasks.append(fetch_times(session, date_val, resve_id, key))

    results = await asyncio.gather(*tasks)

    # 결과 병합
    availability = {}
    idx = 0

    # 이번 달 데이터
    for d in range(start_day, last_day_this_month + 1):
        date_key = f"{start_year}{start_month:02d}{d:02d}"
        if results[idx]:
            availability[date_key] = results[idx]
        idx += 1

    # 다음 달 데이터
    for d in range(1, last_day_next_month + 1):
        date_key = f"{next_year}{next_month:02d}{d:02d}"
        if results[idx]:
            availability[date_key] = results[idx]
        idx += 1

    return availability


async def run_all(key="4236"):
    async with aiohttp.ClientSession() as session:
        facilities = await fetch_facilities(session, key)

        tasks = []
        for rid in facilities:
            tasks.append(fetch_availability(session, rid, key))

        results = await asyncio.gather(*tasks)

        availability_map = {
            rid: data for (rid, info), data in zip(facilities.items(), results)
        }

        return facilities, availability_map
