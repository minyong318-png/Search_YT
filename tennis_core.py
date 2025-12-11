import aiohttp
import asyncio
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import calendar

BASE_URL = "https://publicsports.yongin.go.kr"


# -------------------------------------------
# TCP Connection Pool 최적화
# -------------------------------------------
def get_connector():
    return aiohttp.TCPConnector(limit=30, ssl=False)


# -------------------------------------------
# fetch 유틸
# -------------------------------------------
async def fetch(session, url, method="GET", data=None, params=None):
    try:
        async with session.request(method, url, params=params, data=data) as resp:
            return await resp.text()
    except:
        return None


# -------------------------------------------
# 시설명 정리 (가독성 개선)
# -------------------------------------------
def normalize_title(title):
    mapping = {
        "포곡": "포곡읍 테니스장",
        "백암": "백암 테니스장",
        "양지": "양지면 테니스장",
        "이동": "이동읍 테니스장",
        "남사": "남사읍 테니스장",
        "모현": "모현읍 테니스장",
    }

    for key, nice in mapping.items():
        if key in title:
            return nice
    return title


# -------------------------------------------
# 전체 페이지 수 파악 → 모든 페이지 크롤링
# -------------------------------------------
async def fetch_facilities(session, key):
    url = f"{BASE_URL}/publicsports/sports/selectFcltyRceptResveListU.do"

    facilities = {}
    page = 1
    total_pages = None

    while True:
        params = {
            "key": key,
            "pageUnit": 8,
            "pageIndex": page,
            "checkSearchMonthNow": "false"
        }

        html = await fetch(session, url, method="GET", params=params)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")

        # ----- 전체 페이지 수 파악 -----
        if total_pages is None:
            btns = soup.select("a[href*='fn_link_page']")
            nums = []
            for a in btns:
                m = re.search(r"fn_link_page\((\d+)\)", a.get("href", ""))
                if m:
                    nums.append(int(m.group(1)))
            total_pages = max(nums) if nums else 1

        # ----- 시설 목록 추출 -----
        items = soup.select("li.reserve_box_item")
        if not items:  # 페이지 없음
            break

        for li in items:
            a = li.select_one("div.btn_wrap a[href*='selectFcltyRceptResveViewU.do']")
            if not a:
                continue

            href = a.get("href", "")
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
            title = normalize_title(title)

            # 테니스 관련 시설만
            if ("테니스" in title) or ("코트" in title):
                facilities[rid] = {"title": title, "location": location}

        if page >= total_pages:
            break
        page += 1

    return facilities


# -------------------------------------------
# 날짜별 시간표 데이터 가져오기
# -------------------------------------------
async def fetch_times(session, date_val, resve_id):
    url = f"{BASE_URL}/publicsports/sports/selectRegistTimeByChosenDateFcltyRceptResveApply.do"
    data = {"dateVal": date_val, "resveId": resve_id}

    try:
        async with session.post(url, data=data) as resp:
            j = await resp.json()
            return j.get("resveTmList", [])
    except:
        return []


# -------------------------------------------
# 한 시설 전체 기간 조회 (오늘 → 다음달 마지막날)
# -------------------------------------------
async def fetch_availability(session, resve_id):
    today = datetime.today()

    # 이번달
    start_year = today.year
    start_month = today.month
    start_day = today.day
    last_day_this = calendar.monthrange(start_year, start_month)[1]

    # 다음달
    next_date = today.replace(day=1) + timedelta(days=32)
    next_year = next_date.year
    next_month = next_date.month
    last_day_next = calendar.monthrange(next_year, next_month)[1]

    tasks = []

    # 이번달 (오늘→월말)
    for d in range(start_day, last_day_this + 1):
        date_val = f"{start_year}{start_month:02d}{d:02d}"
        tasks.append(fetch_times(session, date_val, resve_id))

    # 다음달 전체
    for d in range(1, last_day_next + 1):
        date_val = f"{next_year}{next_month:02d}{d:02d}"
        tasks.append(fetch_times(session, date_val, resve_id))

    results = await asyncio.gather(*tasks)

    availability = {}
    idx = 0

    # 이번달
    for d in range(start_day, last_day_this + 1):
        date_key = f"{start_year}{start_month:02d}{d:02d}"
        if results[idx]:
            availability[date_key] = results[idx]
        idx += 1

    # 다음달
    for d in range(1, last_day_next + 1):
        date_key = f"{next_year}{next_month:02d}{d:02d}"
        if results[idx]:
            availability[date_key] = results[idx]
        idx += 1

    return availability


# -------------------------------------------
# 전체 실행
# -------------------------------------------
async def run_all(key="4236"):
    async with aiohttp.ClientSession(connector=get_connector()) as session:
        facilities = await fetch_facilities(session, key)

        tasks = []
        for rid in facilities:
            tasks.append(fetch_availability(session, rid))

        results = await asyncio.gather(*tasks)

        availability_map = {
            rid: data for (rid, info), data in zip(facilities.items(), results)
        }

        return facilities, availability_map
