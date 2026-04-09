from __future__ import annotations

import argparse
import os
import re
import json
import time
import logging
from datetime import datetime, timezone, timedelta
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
STATS_FILE = os.environ.get("STATS_FILE", "/tmp/pika_stats.json")
KST = timezone(timedelta(hours=9))


def load_stats() -> dict:
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"checks": 0, "alerts": 0}


def save_stats(stats: dict):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json, text/html, */*",
        "Referer": SITE_URL,
    })
    session.get(SITE_URL, timeout=10)
    return session


def fetch_newest_items(session: requests.Session, count: int = 7) -> list[dict]:
    resp = session.get(SEARCH_URL, params=SEARCH_PARAMS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    return items[:count]


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


def send_daily_summary():
    stats = load_stats()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    text = (
        f"*📊 일일 모니터링 리포트 ({today})*\n"
        f"*확인 건수:* {stats['checks']}건\n"
        f"*알림 발송:* {stats['alerts']}건"
    )
    resp = requests.post(SLACK_WEBHOOK, json={"text": text}, timeout=10)
    if resp.status_code == 200:
        log.info("일일 리포트 전송 완료")
    else:
        log.error("일일 리포트 전송 실패: %s %s", resp.status_code, resp.text)

    save_stats({"checks": 0, "alerts": 0})


def load_alerted_ids() -> set:
    try:
        with open(STATS_FILE) as f:
            data = json.load(f)
        return set(data.get("alerted_ids", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_alerted_ids(stats: dict, alerted_ids: set):
    stats["alerted_ids"] = list(alerted_ids)
    save_stats(stats)


def check_once():
    stats = load_stats()
    stats["checks"] += 1
    alerted_ids = set(stats.get("alerted_ids", []))

    session = get_session()
    items = fetch_newest_items(session, count=7)
    if not items:
        log.info("검색 결과 없음")
        save_alerted_ids(stats, alerted_ids)
        return

    log.info("최신 %d개 리스팅 탐색 시작", len(items))

    for idx, item in enumerate(items, 1):
        item_id = item.get("itemId") or item.get("asin")
        title = item.get("title", "")
        log.info("[%d/%d] [%s] %s", idx, len(items), item_id, title[:50])

        if item_id in alerted_ids:
            log.info("  → 이미 알림 보낸 아이템 — 스킵")
            continue

        price_gbp = item.get("priceRaw", 0)
        available_qty = fetch_detail(session, item_id)
        log.info("  → 수량: %d개, 가격: £%.2f", available_qty, price_gbp)

        if available_qty >= 2 and price_gbp <= MAX_PRICE_GBP:
            log.info("  ✅ 조건 충족! 알림 전송")
            send_slack_alert(item, available_qty, price_gbp)
            stats["alerts"] += 1
            alerted_ids.add(item_id)
        else:
            reasons = []
            if available_qty < 2:
                reasons.append(f"수량 {available_qty}개 (2개 이상 필요)")
            if price_gbp > MAX_PRICE_GBP:
                reasons.append(f"가격 £{price_gbp:.2f} (£11 이하 필요)")
            log.info("  ❌ 조건 미충족: %s", ", ".join(reasons))

    save_alerted_ids(stats, alerted_ids)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    if args.summary:
        send_daily_summary()
    else:
        check_once()
