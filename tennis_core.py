import aiohttp
import asyncio
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import calendar

BASE_URL = "https://publicsports.yongin.go.kr"

JSESSIONID = "3712D353323652076FA29988A7950583.tomcat1"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://publicsports.yongin.go.kr/publicsports/sports/selectFcltyRceptResveListU.do"
}

COOKIES = {
    "JSESSIONID": JSESSIONID
}


def get_connector():
    return aiohttp.TCPConnector(limit=50, ssl=False)


async def fetch_html(session, url, params=None):
    try:
        async with session.get(url, params=params) as resp:
            return await resp.text()
    except:
        return ""


# -----------------------------------------------------
# 페이지네이션: HTML의 pageIndex=숫자 기반
# -----------------------------------------------------
async def fetch_facilities(session, key="4236"):
    url = f"{BASE_URL}/publicsports/sports/selectFcltyRceptResveListU.do"

    facilities = {}
    page = 1
    max_page = None

    # --- 테니스 필터는 POST 요청 ---
    first_payload = {
        "searchResveType": "03",      # ★ 테니스 필터 핵심
        "searchGubun": "전체",
        "searchArea": "전체",
        "searchUse": "전체",
        "checkSearchMonthNow": "false",
        "pageIndex": 1,
        "pageUnit": 8
    }
    # 1) 첫 페이지 요청
    async with session.post(url, data=first_payload) as resp:
        html = await resp.text()

    # 2) pageIndex=숫자 전체 추출 → max_page 계산
    page_indices = re.findall(r"pageIndex=(\d+)", html)
    max_page = max(int(p) for p in page_indices) if page_indices else 1

    facilities.update(parse_facility_html(html))

    # 3) 나머지 페이지 요청
    for page in range(2, max_page + 1):
        payload = dict(first_payload)
        payload["pageIndex"] = page

        async with session.post(url, data=payload) as resp:
            html = await resp.text()
            facilities.update(parse_facility_html(html))

    return facilities


# -----------------------------------------------------
# HTML → 시설 목록 파싱
# -----------------------------------------------------
def parse_facility_html(html):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("li.reserve_box_item")

    results = {}
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

        results[rid] = {"title": title, "location": location}

    return results


# -----------------------------------------------------
# 날짜별 시간표
# -----------------------------------------------------
async def fetch_times(session, date_val, rid):
    url = f"{BASE_URL}/publicsports/sports/selectRegistTimeByChosenDateFcltyRceptResveApply.do"
    data = {"dateVal": date_val, "resveId": rid}

    try:
        async with session.post(url, data=data) as resp:
            j = await resp.json()
            return j.get("resveTmList", [])
    except:
        return []


# -----------------------------------------------------
# 날짜 범위
# -----------------------------------------------------
async def fetch_availability(session, rid):
    today = datetime.today()
    result = {}

    y, m, d0 = today.year, today.month, today.day
    last_this = calendar.monthrange(y, m)[1]

    nd = today.replace(day=1) + timedelta(days=32)
    ny, nm = nd.year, nd.month
    last_next = calendar.monthrange(ny, nm)[1]

    tasks = []
    for d in range(d0, last_this + 1):
        tasks.append(fetch_times(session, f"{y}{m:02d}{d:02d}", rid))

    for d in range(1, last_next + 1):
        tasks.append(fetch_times(session, f"{ny}{nm:02d}{d:02d}", rid))

    times = await asyncio.gather(*tasks)

    idx = 0
    for d in range(d0, last_this + 1):
        key = f"{y}{m:02d}{d:02d}"
        if times[idx]:
            result[key] = times[idx]
        idx += 1

    for d in range(1, last_next + 1):
        key = f"{ny}{nm:02d}{d:02d}"
        if times[idx]:
            result[key] = times[idx]
        idx += 1

    return result


# -----------------------------------------------------
# 전체 실행
# -----------------------------------------------------
async def run_all_async():
    async with aiohttp.ClientSession(
        connector=get_connector(),
        headers=HEADERS,
        cookies=COOKIES
    ) as session:

        facilities = await fetch_facilities(session)

        tasks = []
        for rid in facilities:
            tasks.append(fetch_availability(session, rid))

        avail_list = await asyncio.gather(*tasks)

        availability = {}
        for (rid, info), data in zip(facilities.items(), avail_list):
            if data:
                availability[rid] = data

        return facilities, availability


def run_all():
    return asyncio.run(run_all_async())
