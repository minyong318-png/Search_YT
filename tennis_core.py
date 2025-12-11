import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import calendar
import re


BASE_URL = "https://publicsports.yongin.go.kr"


class YonginTennisCrawler:
    def __init__(self):
        self.session = requests.Session()
        self.set_manual_cookie()

        # 기본 헤더
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://publicsports.yongin.go.kr/publicsports/sports/selectFcltyRceptResveListU.do"
        })

    # ★ 네 원본 쿠키 그대로 삽입
    def set_manual_cookie(self):
        self.session.cookies.clear()
        self.session.cookies.set(
            "JSESSIONID",
            "3712D353323652076FA29988A7950583.tomcat1",
            domain="publicsports.yongin.go.kr"
        )

    # ------------------------------
    # 전체 페이지를 끝까지 순회
    # ------------------------------
    def fetch_facilities(self, key="4236"):
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

            res = self.session.get(url, params=params)
            soup = BeautifulSoup(res.text, "html.parser")

            # 전체 페이지 수 파악
            if total_pages is None:
                btns = soup.select("a[href*='fn_link_page']")
                nums = []
                for a in btns:
                    m = re.search(r"fn_link_page\((\d+)\)", a.get("href", ""))
                    if m:
                        nums.append(int(m.group(1)))
                total_pages = max(nums) if nums else 1

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

                facilities[rid] = {
                    "title": title,
                    "location": location
                }

            if page >= total_pages:
                break
            page += 1

        return facilities

    # ------------------------------
    # 날짜별 시간표 요청
    # ------------------------------
    def fetch_times(self, date_val, rid):
        url = f"{BASE_URL}/publicsports/sports/selectRegistTimeByChosenDateFcltyRceptResveApply.do"
        data = {
            "dateVal": date_val,
            "resveId": rid
        }

        res = self.session.post(url, data=data)
        try:
            j = res.json()
            return j.get("resveTmList", [])
        except:
            return []

    # ------------------------------
    # 오늘~다음달말 조회
    # ------------------------------
    def fetch_availability(self, rid):
        today = datetime.today()
        result = {}

        # 이번달
        y = today.year
        m = today.month
        d0 = today.day
        last_this = calendar.monthrange(y, m)[1]

        # 다음달
        next_dt = today.replace(day=1) + timedelta(days=32)
        ny = next_dt.year
        nm = next_dt.month
        last_next = calendar.monthrange(ny, nm)[1]

        # 이번달
        for d in range(d0, last_this + 1):
            date_val = f"{y}{m:02d}{d:02d}"
            t = self.fetch_times(date_val, rid)
            if t:
                result[date_val] = t

        # 다음달
        for d in range(1, last_next + 1):
            date_val = f"{ny}{nm:02d}{d:02d}"
            t = self.fetch_times(date_val, rid)
            if t:
                result[date_val] = t

        return result


# ------------------------------
# 외부에서 호출하는 함수
# ------------------------------
def run_all():
    crawler = YonginTennisCrawler()
    fac = crawler.fetch_facilities()

    availability = {}
    for rid in fac:
        times = crawler.fetch_availability(rid)
        if times:  # 시간표 있으면 테니스장 확정
            availability[rid] = times

    return fac, availability
