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

MAX_PRICE_GBP = 11


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


def fetch_detail(session: requests.Session, item_id: str) -> int:
    """Returns available_quantity."""
    resp = session.get(DETAIL_URL + f"/{item_id}", params={"country": "GB"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    html = data.get("html", "")
    qty_match = re.search(r"changeQuantity\(1,\s*(\d+)\)", html)
    return int(qty_match.group(1)) if qty_match else 1


def send_slack_alert(item: dict, available_qty: int, price_gbp: float):
    item_id = item.get("itemId") or item.get("asin")
    title = item.get("title", "Unknown")
    az365_link = f"{SITE_URL}/ebay/detail/{item_id}?country=GB"

    text = (
        f"*🔔 피카츄 뮤지엄 알림!*\n"
        f"*상품명:* {title}\n"
        f"*가격:* £{price_gbp:.2f}\n"
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

    price_gbp = item.get("priceRaw", 0)
    available_qty = fetch_detail(session, item_id)
    log.info("수량: %d개, 가격: £%.2f", available_qty, price_gbp)

    if available_qty >= 2 and price_gbp <= MAX_PRICE_GBP:
        log.info("✅ 조건 충족! 알림 전송")
        send_slack_alert(item, available_qty, price_gbp)
        last_alerted_item_id = item_id
    else:
        reasons = []
        if available_qty < 2:
            reasons.append(f"수량 {available_qty}개 (2개 이상 필요)")
        if price_gbp > MAX_PRICE_GBP:
            reasons.append(f"가격 £{price_gbp:.2f} (£11 이하 필요)")
        log.info("❌ 조건 미충족: %s", ", ".join(reasons))


if __name__ == "__main__":
    check_once()
