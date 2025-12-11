import aiohttp
import asyncio
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import calendar

BASE_URL = "https://publicsports.yongin.go.kr"


# -------------------------------------------
# HTTP Client
# -------------------------------------------
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


# -------------------------------------------
# 시설 목록 수집
# -------------------------------------------
async def fetch_facilities(session, key):
    url = f"{BASE_URL}/publicsports/sports/selectFcltyRceptResveListU.do"
    params = {
        "key": key,
        "pageUnit": 8,
        "checkSearchMonthNow": "false",
        "pageIndex": 1,
    }

    async with session.get(url, params=params, ssl=False) as resp:
        html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")

    res = {}
    for li in soup.select("li.reserve_box_item"):
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

        if "테니스" in title:
            res[rid] = {"title": title, "location": location}

    return res


# -------------------------------------------
# 날짜별 시간 조회
# -------------------------------------------
async def fetch_times(session, date_val, resve_id, key):
    url = f"{BASE_URL}/publicsports/sports/selectRegistTimeByChosenDateFcltyRceptResveApply.do"

    data = {
        "dateVal": date_val,
        "resveId": resve_id,
    }

    async with session.post(url, data=data, ssl=False) as resp:
        try:
            j = await resp.json()
            return j.get("resveTmList", [])
        except:
            return []


# -------------------------------------------
# 한 코트 전체 수집
# -------------------------------------------
async def fetch_availability(session, resve_id, key):
    today = datetime.today()

    months = []
    months.append((today.year, today.month))
    next_month = (today.replace(day=1) + timedelta(days=32))
    months.append((next_month.year, next_month.month))

    tasks = []
    for y, m in months:
        last = calendar.monthrange(y, m)[1]
        for d in range(1, last + 1):
            date_val = f"{y}{m:02d}{d:02d}"
            tasks.append(fetch_times(session, date_val, resve_id, key))

    results = await asyncio.gather(*tasks)

    idx = 0
    availability = {}
    for y, m in months:
        last = calendar.monthrange(y, m)[1]
        for d in range(1, last + 1):
            date_val = f"{y}{m:02d}{d:02d}"
            if results[idx]:
                availability[date_val] = results[idx]
            idx += 1

    return availability


# -------------------------------------------
# 전체 orchestration
# -------------------------------------------
async def run_all(key="4236"):
    async with aiohttp.ClientSession() as session:
        facilities = await fetch_facilities(session, key)

        tasks = []
        for rid in facilities:
            tasks.append(fetch_availability(session, rid, key))

        results = await asyncio.gather(*tasks)

        availability_map = {}
        for (rid, info), data in zip(facilities.items(), results):
            availability_map[rid] = data

        return facilities, availability_map
