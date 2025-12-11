import aiohttp
import asyncio
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import calendar

# 테니스 시설 목록 endpoint
BASE_URL = "https://publicsports.yongin.go.kr/publicsports/sports/selectFcltyRceptResveListU.do"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": BASE_URL
}

def get_connector():
    return aiohttp.TCPConnector(limit=60, ssl=False)


# --------------------------------------------------------------
# ★ 자동 쿠키 갱신: 첫 요청에서 서버가 내려주는 쿠키를 session에 저장
# --------------------------------------------------------------
async def init_session(session):
    async with session.get(BASE_URL, params={"pageIndex":1}) as resp:
        set_cookie = resp.cookies.get("JSESSIONID")
        if set_cookie:
            session.cookie_jar.update_cookies({"JSESSIONID": set_cookie.value})
            print("[INFO] New JSESSIONID:", set_cookie.value)
        else:
            print("[WARN] 서버에서 쿠키를 내려주지 않음")


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
# 시설 HTML 파싱
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
# ① 테니스 시설 전체 페이지 크롤링
# --------------------------------------------------------------
async def fetch_facilities(session):

    facilities = {}

    base_params = {
        "searchFcltyFieldNm": "ITEM_01",  # ★ 테니스 필터
        "pageUnit": 20,
        "pageIndex": 1,
        "checkSearchMonthNow": "false"
    }

    # 1) 첫 페이지 요청 + 자동 쿠키 갱신 적용됨
    html = await fetch_html(session, BASE_URL, params=base_params)
    if not html:
        print("[ERROR] 첫 페이지 가져오기 실패")
        return facilities

    # 2) pageIndex=숫자 전체 추출 → 마지막 페이지 파악
    page_indices = re.findall(r"pageIndex=(\d+)", html)
    max_page = max(int(p) for p in page_indices) if page_indices else 1

    print(f"[INFO] 총 페이지 수: {max_page}")

    # 3) 첫 페이지 파싱
    facilities.update(parse_facility_html(html))

    # 4) 나머지 페이지 병렬 요청
    tasks = []
    for page in range(2, max_page + 1):
        params2 = dict(base_params)
        params2["pageIndex"] = page
        tasks.append(fetch_html(session, BASE_URL, params=params2))

    pages_html = await asyncio.gather(*tasks)
    for html in pages_html:
        if html:
            facilities.update(parse_facility_html(html))

    return facilities


# --------------------------------------------------------------
# ② 날짜별 시간 조회
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
# ③ 내일 ~ 다음달 끝까지
# --------------------------------------------------------------
async def fetch_availability(session, rid):
    today = datetime.today()
    start = today + timedelta(days=1)  # ★ 오늘 제외

    result = {}

    y, m = start.year, start.month
    last_this = calendar.monthrange(y, m)[1]

    next_dt = start.replace(day=1) + timedelta(days=32)
    ny, nm = next_dt.year, next_dt.month
    last_next = calendar.monthrange(ny, nm)[1]

    tasks = []

    # 이번달
    for d in range(start.day, last_this + 1):
        tasks.append(fetch_times(session, f"{y}{m:02d}{d:02d}", rid))

    # 다음달
    for d in range(1, last_next + 1):
        tasks.append(fetch_times(session, f"{ny}{nm:02d}{d:02d}", rid))

    times_list = await asyncio.gather(*tasks)

    idx = 0
    for d in range(start.day, last_this + 1):
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
# 전체 실행
# --------------------------------------------------------------
async def run_all_async():
    async with aiohttp.ClientSession(
        connector=get_connector(),
        headers=HEADERS
    ) as session:

        # ★ 1) 세션 시작 → 자동 쿠키 갱신
        await init_session(session)

        # ★ 2) 전체 테니스 시설 크롤링
        facilities = await fetch_facilities(session)

        # ★ 3) 각 시설 날짜 데이터 병렬 처리
        tasks = [fetch_availability(session, rid) for rid in facilities]
        results = await asyncio.gather(*tasks)

        availability = {
            rid: data
            for (rid, _), data in zip(facilities.items(), results)
            if data
        }

        return facilities, availability


def run_all():
    return asyncio.run(run_all_async())
