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

    # 1) 첫 페이지 요청
    params = {
        "key": key,
        "pageUnit": 8,
        "pageIndex": 1,
        "checkSearchMonthNow": "false"
    }

    html = await fetch_html(session, url, params=params)
    if not html:
        print("[ERROR] 1페이지 HTML 수신 실패")
        return facilities

    # 2) 페이지 번호 전체 추출
    page_indices = re.findall(r"pageIndex=(\d+)", html)
    if page_indices:
        max_page = max(int(p) for p in page_indices)
    else:
        max_page = 1

    print("발견된 마지막 페이지 번호:", max_page)

    # 3) 첫 페이지 처리
    facilities.update(parse_facility_html(html))

    # 4) 2페이지 ~ 마지막 페이지 순회
    tasks = []
    for page in range(2, max_page + 1):
        params2 = {
            "key": key,
            "pageUnit": 8,
            "pageIndex": page,
            "checkSearchMonthNow": "false"
        }
        tasks.append(fetch_html(session, url, params=params2))

    pages_html = await asyncio.gather(*tasks)

    for html in pages_html:
        if html:
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
