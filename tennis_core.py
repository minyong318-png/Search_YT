import aiohttp
import asyncio
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import calendar

BASE_URL = "https://publicsports.yongin.go.kr/publicsports/sports/selectFcltyRceptResveListU.do"

# 네가 쓰던 쿠키(서버에서 페이지 보안 때문에 필요)
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


# --------------------------------------------------------------
# HTML 요청
# --------------------------------------------------------------
async def fetch_html(session, url, params=None):
    try:
        async with session.get(url, params=params) as resp:
            return await resp.text()
    except Exception as e:
        print("[ERROR] fetch_html:", e)
        return ""


# --------------------------------------------------------------
# HTML → 시설 리스트 파싱
# --------------------------------------------------------------
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


# --------------------------------------------------------------
# ① 시설 목록 전체 페이지 크롤링 (테니스 필터 포함)
# --------------------------------------------------------------
async def fetch_facilities(session):

    facilities = {}

    # ★ 테니스 필터: ITEM_01 (중요!)
    base_params = {
        "searchFcltyFieldNm": "ITEM_01",      # 테니스 필터
        "pageUnit": 20,
        "pageIndex": 1,
        "checkSearchMonthNow": "false"
    }

    # ---- 1) 1페이지 요청 ----
    html = await fetch_html(session, BASE_URL, params=base_params)

    if not html:
        print("[ERROR] 1페이지 불러오기 실패")
        return facilities

    # ---- 2) pageIndex=숫자 전체 찾기 (원본 로직 동일) ----
    page_indices = re.findall(r"pageIndex=(\d+)", html)
    if page_indices:
        max_page = max(int(p) for p in page_indices)
    else:
        max_page = 1

    print(f"[INFO] 시설 목록 마지막 페이지: {max_page}")

    # ---- 3) 1페이지 파싱 ----
    facilities.update(parse_facility_html(html))

    # ---- 4) 나머지 페이지 2 ~ max_page ----
    tasks = []
    for page in range(2, max_page + 1):
        params = dict(base_params)
        params["pageIndex"] = page
        tasks.append(fetch_html(session, BASE_URL, params=params))

    pages_html = await asyncio.gather(*tasks)

    for html in pages_html:
        if html:
            facilities.update(parse_facility_html(html))

    return facilities


# --------------------------------------------------------------
# ② 날짜별 시간표 조회
# --------------------------------------------------------------
async def fetch_times(session, date_val, rid):
    url = "https://publicsports.yongin.go.kr/publicsports/sports/selectRegistTimeByChosenDateFcltyRceptResveApply.do"
    data = {"dateVal": date_val, "resveId": rid}

    try:
        async with session.post(url, data=data) as resp:
            j = await resp.json()
            return j.get("resveTmList", [])
    except:
        return []


# --------------------------------------------------------------
# ③ 오늘 ~ 다음달 말 날짜별 예약현황
# --------------------------------------------------------------
async def fetch_availability(session, rid):
    today = datetime.today()
    result = {}

    # 이번달
    y, m, d0 = today.year, today.month, today.day
    last_this = calendar.monthrange(y, m)[1]

    # 다음달
    nd = today.replace(day=1) + timedelta(days=32)
    ny, nm = nd.year, nd.month
    last_next = calendar.monthrange(ny, nm)[1]

    tasks = []

    for d in range(d0, last_this + 1):
        date_val = f"{y}{m:02d}{d:02d}"
        tasks.append(fetch_times(session, date_val, rid))

    for d in range(1, last_next + 1):
        date_val = f"{ny}{nm:02d}{d:02d}"
        tasks.append(fetch_times(session, date_val, rid))

    times_list = await asyncio.gather(*tasks)

    idx = 0
    for d in range(d0, last_this + 1):
        key = f"{y}{m:02d}{d:02d}"
        if times_list[idx]:
            result[key] = times_list[idx]
        idx += 1

    for d in range(1, last_next + 1):
        key = f"{ny}{nm:02d}{d:02d}"
        if times_list[idx]:
            result[key] = times_list[idx]
        idx += 1

    return result


# --------------------------------------------------------------
# 전체 실행 함수
# --------------------------------------------------------------
async def run_all_async():
    async with aiohttp.ClientSession(
        connector=get_connector(),
        headers=HEADERS,
        cookies=COOKIES
    ) as session:

        # 1) 시설(테니스) 전체 수집
        facilities = await fetch_facilities(session)

        # 2) 각 시설별 날짜별 예약현황 수집
        tasks = []
        for rid in facilities:
            tasks.append(fetch_availability(session, rid))

        results = await asyncio.gather(*tasks)

        availability = {}
        for (rid, info), data in zip(facilities.items(), results):
            if data:    # 일정이 하나라도 있으면 테니스 시설 확정
                availability[rid] = data

        return facilities, availability


def run_all():
    return asyncio.run(run_all_async())
