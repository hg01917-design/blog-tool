"""주문 알림 모듈 — 각 쇼핑몰 신규 주문 감지 + 카카오 알림톡 발송."""

import os
import json
import threading
import time
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify

orders_bp = Blueprint("orders", __name__)

# ──────────────────────────────────────────────
# 설정 파일 경로
# ──────────────────────────────────────────────
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "data")
ORDERS_CONFIG_FILE = os.path.join(CONFIG_DIR, "orders_config.json")
ORDERS_DATA_FILE = os.path.join(CONFIG_DIR, "orders_data.json")

os.makedirs(CONFIG_DIR, exist_ok=True)

# ──────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────
DEFAULT_CONFIG = {
    "notification_enabled": True,
    "check_interval_seconds": 60,
    "kakao_api_key": "",
    "kakao_sender_key": "",
    "kakao_template_code": "",
    "recipient_phone": "",
    "message_template": "{쇼핑몰}에 새 주문이 들어왔어요!\n상품명: {상품명}\n수량: {수량}개\n주문자: {주문자}",
    "platforms": {
        "smartstore": {
            "name": "스마트스토어",
            "enabled": False,
            "client_id": "",
            "client_secret": "",
        },
        "ably": {
            "name": "에이블리",
            "enabled": False,
            "api_key": "",
        },
        "zigzag": {
            "name": "지그재그",
            "enabled": False,
            "api_key": "",
        },
        "lotteon": {
            "name": "롯데온",
            "enabled": False,
            "api_key": "",
        },
        "cafe24": {
            "name": "카페24",
            "enabled": False,
            "mall_id": "",
            "client_id": "",
            "client_secret": "",
        },
        "kakao_checkout": {
            "name": "카카오체크아웃",
            "enabled": False,
            "api_key": "",
        },
    },
}


def _load_config():
    if os.path.exists(ORDERS_CONFIG_FILE):
        with open(ORDERS_CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 기본값 병합 (새 키가 추가됐을 때 대비)
        merged = {**DEFAULT_CONFIG, **saved}
        merged["platforms"] = {**DEFAULT_CONFIG["platforms"], **saved.get("platforms", {})}
        return merged
    return DEFAULT_CONFIG.copy()


def _save_config(config):
    with open(ORDERS_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _load_orders():
    if os.path.exists(ORDERS_DATA_FILE):
        with open(ORDERS_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_orders(orders):
    with open(ORDERS_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 카카오 알림톡 발송
# ──────────────────────────────────────────────
def _send_kakao_notification(config, order):
    """카카오 알림톡 API로 주문 알림 발송."""
    import requests as http_requests

    api_key = config.get("kakao_api_key", "")
    sender_key = config.get("kakao_sender_key", "")
    template_code = config.get("kakao_template_code", "")
    phone = config.get("recipient_phone", "")

    if not all([api_key, sender_key, phone]):
        return {"status": "skipped", "reason": "카카오 알림톡 설정 미완료"}

    # 메시지 템플릿 변수 치환
    template = config.get("message_template", DEFAULT_CONFIG["message_template"])
    message = template.format(
        쇼핑몰=order.get("platform_name", ""),
        상품명=order.get("product_name", ""),
        수량=order.get("quantity", 0),
        주문자=order.get("buyer_name", ""),
    )

    # 알림톡 API 호출 (비즈엠 기준)
    try:
        resp = http_requests.post(
            "https://alimtalk-api.bizmsg.kr/v2/sender/send",
            headers={
                "Content-Type": "application/json",
                "userId": api_key,
            },
            json={
                "senderKey": sender_key,
                "templateCode": template_code,
                "recipientList": [
                    {
                        "recipientNo": phone,
                        "templateParameter": {
                            "쇼핑몰": order.get("platform_name", ""),
                            "상품명": order.get("product_name", ""),
                            "수량": str(order.get("quantity", 0)),
                            "주문자": order.get("buyer_name", ""),
                        },
                    }
                ],
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return {"status": "success"}
        return {"status": "failed", "error": resp.text[:200]}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# ──────────────────────────────────────────────
# 쇼핑몰 API 주문 조회 (구조만 — 실제 API 연동 시 구현)
# ──────────────────────────────────────────────
def _fetch_smartstore_orders(platform_config):
    """스마트스토어 커머스 API로 신규 주문 조회."""
    client_id = platform_config.get("client_id", "")
    client_secret = platform_config.get("client_secret", "")
    if not client_id or not client_secret:
        return []

    import requests as http_requests
    try:
        # 1) 인증 토큰 발급
        token_resp = http_requests.post(
            "https://api.commerce.naver.com/external/v1/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "type": "SELLER",
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
        if token_resp.status_code != 200:
            return []
        token = token_resp.json().get("access_token", "")

        # 2) 최근 1시간 신규 주문 조회
        now = datetime.utcnow()
        since = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        resp = http_requests.get(
            "https://api.commerce.naver.com/external/v1/pay-order/seller/product-orders/last-changed-statuses",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "lastChangedFrom": since,
                "lastChangedType": "PAYED",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return []

        orders = []
        for item in resp.json().get("data", {}).get("lastChangeStatuses", []):
            product_order_id = item.get("productOrderId", "")
            # 상세 조회
            detail_resp = http_requests.get(
                f"https://api.commerce.naver.com/external/v1/pay-order/seller/product-orders/{product_order_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if detail_resp.status_code == 200:
                d = detail_resp.json().get("data", {})
                orders.append({
                    "order_id": product_order_id,
                    "platform": "smartstore",
                    "platform_name": "스마트스토어",
                    "product_name": d.get("productName", ""),
                    "quantity": d.get("quantity", 1),
                    "buyer_name": d.get("ordererName", ""),
                    "order_date": d.get("paymentDate", ""),
                    "status": "new",
                })
        return orders
    except Exception:
        return []


def _fetch_cafe24_orders(platform_config):
    """카페24 API로 신규 주문 조회."""
    mall_id = platform_config.get("mall_id", "")
    client_id = platform_config.get("client_id", "")
    client_secret = platform_config.get("client_secret", "")
    if not all([mall_id, client_id, client_secret]):
        return []

    import requests as http_requests
    try:
        now = datetime.utcnow()
        since = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        resp = http_requests.get(
            f"https://{mall_id}.cafe24api.com/api/v2/admin/orders",
            headers={
                "Authorization": f"Bearer {client_id}",
                "Content-Type": "application/json",
            },
            params={
                "start_date": since,
                "order_status": "N10",  # 입금대기/결제완료
                "limit": 50,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return []

        orders = []
        for item in resp.json().get("orders", []):
            orders.append({
                "order_id": item.get("order_id", ""),
                "platform": "cafe24",
                "platform_name": "카페24",
                "product_name": item.get("items", [{}])[0].get("product_name", "") if item.get("items") else "",
                "quantity": item.get("items", [{}])[0].get("quantity", 1) if item.get("items") else 1,
                "buyer_name": item.get("buyer_name", ""),
                "order_date": item.get("order_date", ""),
                "status": "new",
            })
        return orders
    except Exception:
        return []


# 플랫폼별 주문 조회 함수 매핑
PLATFORM_FETCHERS = {
    "smartstore": _fetch_smartstore_orders,
    "cafe24": _fetch_cafe24_orders,
    # ably, zigzag, lotteon, kakao_checkout은 API 문서 확인 후 추가
}


def _check_new_orders():
    """활성화된 모든 플랫폼에서 신규 주문을 확인하고, 알림을 발송합니다."""
    config = _load_config()
    existing_orders = _load_orders()
    existing_ids = {o["order_id"] for o in existing_orders}

    new_orders = []
    for platform_key, platform_config in config["platforms"].items():
        if not platform_config.get("enabled"):
            continue
        fetcher = PLATFORM_FETCHERS.get(platform_key)
        if not fetcher:
            continue
        try:
            fetched = fetcher(platform_config)
            for order in fetched:
                if order["order_id"] not in existing_ids:
                    order["notified_at"] = datetime.now().isoformat()
                    new_orders.append(order)
                    # 카카오 알림톡 발송
                    if config.get("notification_enabled"):
                        _send_kakao_notification(config, order)
        except Exception:
            continue

    if new_orders:
        all_orders = new_orders + existing_orders
        # 최근 500건만 보관
        _save_orders(all_orders[:500])

    return new_orders


# ──────────────────────────────────────────────
# Flask 라우트
# ──────────────────────────────────────────────

@orders_bp.route("/orders")
def orders_page():
    return render_template("orders.html")


@orders_bp.route("/api/orders/config", methods=["GET"])
def get_orders_config():
    config = _load_config()
    # 비밀키는 마스킹
    safe = json.loads(json.dumps(config))
    for p in safe.get("platforms", {}).values():
        for key in ["client_secret", "api_key"]:
            if p.get(key):
                p[key] = p[key][:4] + "****"
    if safe.get("kakao_api_key"):
        safe["kakao_api_key"] = safe["kakao_api_key"][:4] + "****"
    return jsonify(safe)


@orders_bp.route("/api/orders/config", methods=["POST"])
def save_orders_config():
    data = request.get_json()
    config = _load_config()

    # 단순 필드 업데이트
    for key in ["notification_enabled", "check_interval_seconds", "message_template",
                "kakao_api_key", "kakao_sender_key", "kakao_template_code", "recipient_phone"]:
        if key in data:
            val = data[key]
            # 마스킹된 값은 무시
            if isinstance(val, str) and "****" in val:
                continue
            config[key] = val

    # 플랫폼별 설정
    if "platforms" in data:
        for pkey, pval in data["platforms"].items():
            if pkey in config["platforms"]:
                for field, value in pval.items():
                    if isinstance(value, str) and "****" in value:
                        continue
                    config["platforms"][pkey][field] = value

    _save_config(config)
    return jsonify({"success": True})


@orders_bp.route("/api/orders/list")
def get_orders_list():
    orders = _load_orders()
    status_filter = request.args.get("status", "")
    platform_filter = request.args.get("platform", "")

    if status_filter:
        orders = [o for o in orders if o.get("status") == status_filter]
    if platform_filter:
        orders = [o for o in orders if o.get("platform") == platform_filter]

    return jsonify({"orders": orders[:100], "total": len(orders)})


@orders_bp.route("/api/orders/check-now", methods=["POST"])
def check_orders_now():
    """수동으로 신규 주문 확인."""
    new_orders = _check_new_orders()
    return jsonify({"new_count": len(new_orders), "orders": new_orders})


@orders_bp.route("/api/orders/<order_id>/status", methods=["POST"])
def update_order_status(order_id):
    """주문 상태 업데이트 (new → processed 등)."""
    data = request.get_json()
    new_status = data.get("status", "processed")
    orders = _load_orders()
    for order in orders:
        if order.get("order_id") == order_id:
            order["status"] = new_status
            order["updated_at"] = datetime.now().isoformat()
            break
    _save_orders(orders)
    return jsonify({"success": True})


@orders_bp.route("/api/orders/test-notification", methods=["POST"])
def test_notification():
    """테스트 알림 발송."""
    config = _load_config()
    test_order = {
        "platform_name": "테스트",
        "product_name": "테스트 상품",
        "quantity": 1,
        "buyer_name": "하나",
    }
    result = _send_kakao_notification(config, test_order)
    return jsonify(result)


@orders_bp.route("/api/orders/add-test", methods=["POST"])
def add_test_order():
    """테스트용 주문 데이터 추가 (개발/데모용)."""
    data = request.get_json() or {}
    orders = _load_orders()
    test_order = {
        "order_id": f"TEST-{int(time.time())}",
        "platform": data.get("platform", "smartstore"),
        "platform_name": data.get("platform_name", "스마트스토어"),
        "product_name": data.get("product_name", "테스트 상품"),
        "quantity": data.get("quantity", 1),
        "buyer_name": data.get("buyer_name", "홍길동"),
        "order_date": datetime.now().isoformat(),
        "status": "new",
        "notified_at": datetime.now().isoformat(),
    }
    orders.insert(0, test_order)
    _save_orders(orders[:500])
    return jsonify({"success": True, "order": test_order})
