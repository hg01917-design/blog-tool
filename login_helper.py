#!/usr/bin/env python3
"""
로컬 PC에서 실행하는 블로그 로그인 헬퍼 (네이버 + 티스토리).

사용법:
    python login_helper.py --account baremi542
    python login_helper.py --account baremi542 --platform naver
    python login_helper.py --account goodisak --platform tistory
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

PLATFORM_CONFIG = {
    "naver": {
        "login_url": "https://nid.naver.com/nidlogin.login",
        "auth_cookies": ["NID_AUT", "NID_SES"],
        "blog_url": lambda aid: f"https://blog.naver.com/{aid}",
        "login_check": lambda url: "nidlogin" in url or "nid.naver.com" in url,
    },
    "tistory": {
        "login_url": "https://www.tistory.com/auth/login",
        "auth_cookies": ["TSSESSION", "TSESSION"],
        "blog_url": lambda aid: f"https://{aid}.tistory.com/manage",
        "login_check": lambda url: "accounts.kakao.com" in url or "/auth/login" in url,
    },
}


def main():
    parser = argparse.ArgumentParser(description="블로그 로그인 → 쿠키 자동 전송")
    parser.add_argument("--account", required=True, help="계정/블로그 ID (예: baremi542)")
    parser.add_argument("--platform", default="naver", choices=["naver", "tistory"],
                        help="플랫폼 (기본: naver)")
    parser.add_argument("--server", default=DEFAULT_SERVER,
                        help=f"서버 URL (기본: {DEFAULT_SERVER})")
    parser.add_argument("--blog-domain", default="",
                        help="커스텀 도메인 (예: welfer.baremi542.com)")
    args = parser.parse_args()

    account_id = args.account
    platform = args.platform
    server_url = args.server.rstrip("/")
    blog_domain = args.blog_domain.strip()
    config = PLATFORM_CONFIG[platform]

    platform_name = "네이버" if platform == "naver" else "티스토리"
    print(f"[1/4] {platform_name} 로그인 창을 엽니다...")
    print(f"      계정: {account_id}")
    print(f"      플랫폼: {platform}")
    print(f"      서버: {server_url}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="",
            args=["--disable-extensions", "--no-first-run"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            no_viewport=False,
        )
        page = context.new_page()

        try:
            page.goto(config["login_url"], wait_until="domcontentloaded")
            print(f"[2/4] 로그인 페이지가 열렸습니다. 브라우저에서 로그인하세요.")
            print(f"      (브라우저를 닫지 마세요!)")
            print()
            input("      >>> 로그인 완료 후 여기서 Enter를 누르세요 <<<")
            print()

            # 브라우저가 아직 열려있는지 확인
            try:
                page.evaluate("1")
            except Exception:
                print(f"[실패] 브라우저가 닫혔습니다. 다시 실행해주세요.")
                return

            # 블로그 페이지 방문 → 로그인 상태 + 블로그 쿠키 수집
            print(f"[3/4] 로그인 상태를 확인합니다...")
            if blog_domain:
                blog_url = f"https://{blog_domain}/manage"
            else:
                blog_url = config["blog_url"](account_id)
            page.goto(blog_url, wait_until="domcontentloaded")
            time.sleep(2)

            # 로그인 여부 확인
            if config["login_check"](page.url):
                print(f"[실패] 로그인되지 않았습니다. 브라우저에서 로그인을 완료했는지 확인하세요.")
                return

            all_cookies = context.cookies()
            cookie_names = {c["name"] for c in all_cookies}
            auth_found = any(ac in cookie_names for ac in config["auth_cookies"])

            if not auth_found:
                # URL 기반 확인 — 관리 페이지에 접근했으면 로그인 성공
                if platform == "tistory" and "/manage" in page.url:
                    pass  # 로그인 OK
                else:
                    print(f"[실패] 로그인 쿠키가 없습니다. 다시 시도해주세요.")
                    return

            print(f"      로그인 확인 완료! 쿠키 {len(all_cookies)}개 수집")

            # 서버로 쿠키 전송
            print(f"[4/4] 서버로 쿠키를 전송합니다...")
            api_url = f"{server_url}/api/accounts/{account_id}/cookie"

            resp = requests.post(
                api_url,
                json={"cookies": all_cookies},
                headers={"X-Cookie-Token": "blog-tool-cookie-upload"},
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
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
