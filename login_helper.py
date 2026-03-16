#!/usr/bin/env python3
"""
로컬 PC에서 실행하는 네이버 로그인 헬퍼.

사용법:
    python login_helper.py --account baremi542
    python login_helper.py --account baremi542 --server https://app.baremi542.com
"""

import argparse
import json
import sys
import time

try:
    import requests
except ImportError:
    print("requests 패키지가 필요합니다: pip install requests")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright 패키지가 필요합니다:")
    print("  pip install playwright")
    print("  playwright install chromium")
    sys.exit(1)


DEFAULT_SERVER = "https://app.baremi542.com"
LOGIN_URL = "https://nid.naver.com/nidlogin.login"
TIMEOUT_SEC = 120


def main():
    parser = argparse.ArgumentParser(description="네이버 로그인 → 쿠키 자동 전송")
    parser.add_argument("--account", required=True, help="계정 ID (예: baremi542)")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"서버 URL (기본: {DEFAULT_SERVER})")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SEC, help=f"로그인 대기 시간 초 (기본: {TIMEOUT_SEC})")
    args = parser.parse_args()

    account_id = args.account
    server_url = args.server.rstrip("/")
    timeout = args.timeout

    print(f"[1/4] 네이버 로그인 창을 엽니다...")
    print(f"      계정: {account_id}")
    print(f"      서버: {server_url}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            print(f"[2/4] 로그인 페이지가 열렸습니다. 브라우저에서 로그인하세요.")
            print(f"      ({timeout}초 내에 완료해주세요)")
            print()

            # 로그인 완료 대기: NID_AUT 또는 NID_SES 쿠키 감지
            deadline = time.time() + timeout
            logged_in = False
            while time.time() < deadline:
                cookies = context.cookies()
                cookie_names = {c["name"] for c in cookies}
                if "NID_AUT" in cookie_names or "NID_SES" in cookie_names:
                    logged_in = True
                    break
                time.sleep(1)

            if not logged_in:
                print(f"[실패] {timeout}초 내에 로그인이 완료되지 않았습니다.")
                return

            print(f"[3/4] 로그인 감지! 쿠키를 수집합니다...")

            # 블로그 페이지 방문하여 블로그 관련 쿠키도 수집
            page.goto(f"https://blog.naver.com/{account_id}", wait_until="domcontentloaded")
            time.sleep(2)

            all_cookies = context.cookies()
            print(f"      쿠키 {len(all_cookies)}개 수집 완료")

            # 서버로 쿠키 전송
            print(f"[4/4] 서버로 쿠키를 전송합니다...")
            api_url = f"{server_url}/api/accounts/{account_id}/cookie"

            resp = requests.post(
                api_url,
                json={"cookies": all_cookies},
                timeout=30,
            )

            if resp.status_code == 200:
                data = resp.json()
                count = data.get("cookie_count", len(all_cookies))
                print()
                print(f"[완료] 쿠키 {count}개가 서버에 등록되었습니다!")
                print(f"       이제 대시보드에서 발행 버튼을 사용할 수 있습니다.")
            else:
                print(f"[실패] 서버 응답 {resp.status_code}: {resp.text}")

        except KeyboardInterrupt:
            print("\n[취소] 사용자가 중단했습니다.")
        except Exception as e:
            print(f"[오류] {e}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
