import aiohttp
import asyncio
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import calendar

BASE_URL = "https://publicsports.yongin.go.kr"

# 네가 로컬에서 사용하던 쿠키 그대로 넣음
JSESSIONID = "3712D353323652076FA29988A7950583.tomcat1"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://publicsports.yongin.go.kr/publicsports/sports/selectFcltyRceptResveListU.do"
}

COOKIES = {
    "JSESSIONID": JSESSIONID
}


def get_connector():
    # 동시 요청 최적화
    return aiohttp.TCPConnector(limit=50, ssl=False)


# -----------------------------------------------------
# 페이지 HTML 요청 (쿠키 + 헤더 포함)
# -----------------------------------------------------
async def fetch_html(session, url, params=None):
    try:
        async with session.get(url, params=params) as resp:
            return await resp.text()
    except:
        return ""


# -----------------------------------------------------
# 전체 페이지 수 탐색 + 모든 시설 목록 수집
# -----------------------------------------------------
async def fetch_facilities(session, key="4236"):
    url = f"{BASE_URL}/publicsports/sports/selectFcltyRceptResveListU.do"

    facilities = {}
    page = 1
    total_pages = None

    while True:
        params = {
            "key": key,
            "pageUnit": 20,
            "pageIndex": page,
            "checkSearchMonthNow": "false"
        }

        html = await fetch_html(session, url, params=params)
        soup = BeautifulSoup(html, "html.parser")

        # 페이지 개수(1번만 파싱)
        if total_pages is None:
            btns = soup.select("a[href*='fn_link_page']")
            nums = []
            for a in btns:
                m = re.search(r"fn_link_page\((\d+)\)", a.get("href", ""))
                if m:
                    nums.append(int(m.group(1)))
            total_pages = max(nums) if nums else 1

        # 시설 목록 파싱
        items = soup.select("li.reserve_box_item")
        if not items:
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

            # 여기서는 필터 없이 전체 시설을 먼저 수집하고
            facilities[rid] = {"title": title, "location": location}

        if page >= total_pages:
            break
        page += 1

    return facilities


# -----------------------------------------------------
# 날짜별 시간표 조회 (비동기 + POST 요청)
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
# 오늘 ~ 다음달말 전체 날짜 조회
# -----------------------------------------------------
async def fetch_availability(session, rid):
    today = datetime.today()
    result = []

    # 이번달
    y, m, d0 = today.year, today.month, today.day
    last_this = calendar.monthrange(y, m)[1]

    # 다음달
    nd = today.replace(day=1) + timedelta(days=32)
    ny, nm = nd.year, nd.month
    last_next = calendar.monthrange(ny, nm)[1]

    tasks = []

    # 이번달
    for d in range(d0, last_this + 1):
        date_val = f"{y}{m:02d}{d:02d}"
        tasks.append(fetch_times(session, date_val, rid))

    # 다음달
    for d in range(1, last_next + 1):
        date_val = f"{ny}{nm:02d}{d:02d}"
        tasks.append(fetch_times(session, date_val, rid))

    times_list = await asyncio.gather(*tasks)

    availability = {}
    idx = 0

    for d in range(d0, last_this + 1):
        date_key = f"{y}{m:02d}{d:02d}"
        if times_list[idx]:
            availability[date_key] = times_list[idx]
        idx += 1

    for d in range(1, last_next + 1):
        date_key = f"{ny}{nm:02d}{d:02d}"
        if times_list[idx]:
            availability[date_key] = times_list[idx]
        idx += 1

    return availability


# -----------------------------------------------------
# 전체 실행 (비동기 전체 조율)
# -----------------------------------------------------
async def run_all_async():
    async with aiohttp.ClientSession(
        connector=get_connector(),
        headers=HEADERS,
        cookies=COOKIES
    ) as session:

        fac = await fetch_facilities(session)

        tasks = []
        for rid in fac:
            tasks.append(fetch_availability(session, rid))

        results = await asyncio.gather(*tasks)

        availability = {}
        for (rid, info), data in zip(fac.items(), results):
            if data:  # 시간표가 있으면 테니스장 확정
                availability[rid] = data

        return fac, availability


def run_all():
    return asyncio.run(run_all_async())
