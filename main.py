from __future__ import annotations

import os
import re
import json
import time
import logging
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SEARCH_URL = "https://www.az365.co.kr/api/ebay/search"
DETAIL_URL = "https://www.az365.co.kr/api/ebay/detail"
SITE_URL = "https://www.az365.co.kr"
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")

SEARCH_PARAMS = {
    "q": "pikachu at museum",
    "country": "GB",
    "page": "1",
    "sort": "newlyListed",
    "filter": "fixed",
}

MAX_PRICE_KRW = 38000
CHECK_INTERVAL = 300  # 5분

last_alerted_item_id = None


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json, text/html, */*",
        "Referer": SITE_URL,
    })
    session.get(SITE_URL, timeout=10)
    return session


def fetch_newest_item(session: requests.Session) -> dict | None:
    resp = session.get(SEARCH_URL, params=SEARCH_PARAMS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    if not items:
        return None
    return items[0]


def fetch_detail(session: requests.Session, item_id: str) -> tuple[int, float]:
    """Returns (available_quantity, price_krw)."""
    resp = session.get(DETAIL_URL + f"/{item_id}", params={"country": "GB"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    config = data.get("config", {})
    price_usd = config.get("price", 0)
    exchange_rate = config.get("exchangeRate", 1400)
    price_krw = price_usd * exchange_rate

    html = data.get("html", "")
    qty_match = re.search(r"changeQuantity\(1,\s*(\d+)\)", html)
    available_qty = int(qty_match.group(1)) if qty_match else 1

    return available_qty, price_krw


def send_slack_alert(item: dict, available_qty: int, price_krw: float):
    item_id = item.get("itemId") or item.get("asin")
    title = item.get("title", "Unknown")
    price_display = item.get("price", "N/A")
    az365_link = f"{SITE_URL}/ebay/detail/{item_id}?country=GB"

    text = (
        f"*🔔 피카츄 뮤지엄 알림!*\n"
        f"*상품명:* {title}\n"
        f"*현지가:* {price_display}\n"
        f"*원화가:* {price_krw:,.0f}원\n"
        f"*수량:* {available_qty}개 가능\n"
        f"<{az365_link}|바로가기>"
    )

    resp = requests.post(SLACK_WEBHOOK, json={"text": text}, timeout=10)
    if resp.status_code == 200:
        log.info("Slack 알림 전송 완료")
    else:
        log.error("Slack 전송 실패: %s %s", resp.status_code, resp.text)


def check_once():
    global last_alerted_item_id

    session = get_session()
    item = fetch_newest_item(session)
    if not item:
        log.info("검색 결과 없음")
        return

    item_id = item.get("itemId") or item.get("asin")
    title = item.get("title", "")
    log.info("최신 리스팅: [%s] %s", item_id, title[:50])

    if item_id == last_alerted_item_id:
        log.info("이미 알림 보낸 아이템 — 스킵")
        return

    available_qty, price_krw = fetch_detail(session, item_id)
    log.info("수량: %d개, 원화가: %.0f원", available_qty, price_krw)

    if available_qty > 1 and price_krw <= MAX_PRICE_KRW:
        log.info("✅ 조건 충족! 알림 전송")
        send_slack_alert(item, available_qty, price_krw)
        last_alerted_item_id = item_id
    else:
        reasons = []
        if available_qty <= 1:
            reasons.append(f"수량 {available_qty}개 (2개 이상 필요)")
        if price_krw > MAX_PRICE_KRW:
            reasons.append(f"가격 {price_krw:,.0f}원 (38,000원 이하 필요)")
        log.info("❌ 조건 미충족: %s", ", ".join(reasons))


if __name__ == "__main__":
    check_once()
