"""
티스토리 카카오 로그인 반자동 모듈.
- 사용자가 대시보드에서 ID/PW 입력 → Playwright가 대신 타이핑
- 2FA 필요 시 대시보드에서 인증번호 입력 → Playwright가 전달
- 로그인 성공 시 쿠키를 브라우저 프로파일에 자동 저장
"""

import json
import os
import threading
import time
import logging

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

CDP_URL = "http://127.0.0.1:9222"
_APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 세션 상태 저장 (account_id → state)
_sessions = {}
_lock = threading.Lock()


def _is_local_mode() -> bool:
    return os.environ.get("LOCAL_MODE", "").lower() == "true"


def _get_local_chrome_profile() -> str:
    import platform
    system = platform.system()
    if system == "Windows":
        username = os.environ.get("USERNAME", os.environ.get("USER", ""))
        path = f"C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\User Data"
    elif system == "Darwin":
        path = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    else:
        path = os.path.expanduser("~/.config/google-chrome")
    if os.path.isdir(path):
        return path
    raise RuntimeError(f"로컬 크롬 프로필을 찾을 수 없습니다: {path}")


def _get_session(account_id: str) -> dict:
    with _lock:
        return _sessions.get(account_id, {})


def _set_session(account_id: str, data: dict):
    with _lock:
        _sessions[account_id] = data


def get_status(account_id: str) -> dict:
    """현재 로그인 세션 상태를 반환."""
    s = _get_session(account_id)
    if not s:
        return {"status": "idle"}
    return {
        "status": s.get("status", "idle"),
        "message": s.get("message", ""),
    }


def start_login(account_id: str, kakao_id: str, kakao_pw: str):
    """백그라운드 스레드에서 카카오 로그인을 시작."""
    s = _get_session(account_id)
    if s.get("status") in ("running", "waiting_2fa"):
        return {"success": False, "error": "이미 로그인 진행 중입니다."}

    _set_session(account_id, {"status": "running", "message": "로그인 시작..."})

    t = threading.Thread(
        target=_login_worker,
        args=(account_id, kakao_id, kakao_pw),
        daemon=True,
    )
    t.start()
    return {"success": True}


def submit_2fa(account_id: str, code: str) -> dict:
    """2FA 인증번호를 세션에 전달."""
    s = _get_session(account_id)
    if s.get("status") != "waiting_2fa":
        return {"success": False, "error": "2FA 대기 상태가 아닙니다."}

    _set_session(account_id, {
        **s,
        "status": "submitting_2fa",
        "message": "인증번호 입력 중...",
        "2fa_code": code,
    })
    return {"success": True}


def _login_worker(account_id: str, kakao_id: str, kakao_pw: str):
    """Playwright로 카카오 로그인을 수행하는 워커 스레드."""
    try:
        with sync_playwright() as p:
            if _is_local_mode():
                chrome_profile = _get_local_chrome_profile()
                context = p.chromium.launch_persistent_context(
                    user_data_dir=chrome_profile,
                    headless=False,
                    channel="chrome",
                    viewport={"width": 1280, "height": 900},
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = p.chromium.connect_over_cdp(CDP_URL)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()

            try:
                _do_login(account_id, page, kakao_id, kakao_pw)
            finally:
                page.close()
                if _is_local_mode():
                    context.close()

    except Exception as e:
        logger.exception("로그인 워커 에러")
        _set_session(account_id, {
            "status": "failed",
            "message": f"에러: {e}",
        })


def _do_login(account_id: str, page, kakao_id: str, kakao_pw: str):
    """실제 로그인 로직."""
    _set_session(account_id, {"status": "running", "message": "티스토리 로그인 페이지 이동..."})

    # 1. 티스토리 로그인 페이지 → 카카오 로그인 URL 추출 → 직접 이동
    page.goto("https://www.tistory.com/auth/login", wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    _set_session(account_id, {"status": "running", "message": "카카오 로그인 페이지로 이동..."})

    # 카카오 로그인 링크 href 추출 후 직접 이동
    kakao_url = page.evaluate("""() => {
        const link = document.querySelector('a.btn_login.link_kakao_id');
        if (link && link.onclick) {
            // onclick에서 URL 추출 시도
            const match = link.onclick.toString().match(/location\\.href\\s*=\\s*['"]([^'"]+)['"]/);
            if (match) return match[1];
        }
        // Kakao SDK로 생성되는 OAuth URL 직접 구성
        return null;
    }""")

    if kakao_url:
        page.goto(kakao_url, wait_until="domcontentloaded", timeout=30000)
    else:
        # fallback: 카카오 OAuth 인증 URL 직접 이동
        page.goto(
            "https://accounts.kakao.com/login"
            "?continue=https%3A%2F%2Fkauth.kakao.com%2Foauth%2Fauthorize"
            "%3Fclient_id%3D3e6ddd834b023f24221217e370daed18"
            "%26redirect_uri%3Dhttps%253A%252F%252Fwww.tistory.com%252Fauth%252Fkakao%252Fredirect"
            "%26response_type%3Dcode"
            "%26through_account%3Dtrue",
            wait_until="domcontentloaded",
            timeout=30000,
        )
    time.sleep(3)

    # 2. 카카오 ID/PW 입력
    _set_session(account_id, {"status": "running", "message": "카카오 아이디/비밀번호 입력 중..."})

    # 아이디 입력
    id_input = page.locator("input[name='loginId']")
    id_input.wait_for(state="visible", timeout=10000)
    id_input.click()
    time.sleep(0.3)
    id_input.fill("")
    id_input.type(kakao_id, delay=50)
    time.sleep(0.5)

    # 비밀번호 입력
    pw_input = page.locator("input[name='password']")
    pw_input.click()
    time.sleep(0.3)
    pw_input.fill("")
    pw_input.type(kakao_pw, delay=50)
    time.sleep(0.5)

    # 로그인 버튼 클릭 (카카오: button.btn_g.highlight.submit)
    login_btn = page.locator("button.submit, button[type='submit']")
    login_btn.first.click()
    time.sleep(5)

    # 3. 2FA 확인 - 카카오 로그인 후 페이지 상태 분석
    url_after = page.url
    page_text = page.content()

    # 카카오 2FA: 인증번호 입력 또는 카카오톡 인증 페이지
    is_2fa = False
    twofa_input = page.locator("input[name='code'], input[name='passcode'], input[placeholder*='인증'], input[placeholder*='verif'], input[maxlength='4'], input[maxlength='6']")
    if twofa_input.count() > 0:
        is_2fa = True
    elif "인증" in page_text[:5000] or "verify" in page_text[:5000].lower() or "two-step" in page_text[:5000].lower():
        is_2fa = True

    if is_2fa:
        _set_session(account_id, {
            "status": "waiting_2fa",
            "message": "2FA 인증번호를 입력해주세요.",
        })

        # 2FA 코드 대기 (최대 120초)
        code = _wait_for_2fa(account_id, timeout=120)
        if not code:
            _set_session(account_id, {"status": "failed", "message": "2FA 시간 초과"})
            return

        _set_session(account_id, {"status": "running", "message": "인증번호 입력 중..."})

        # 2FA 입력란 다시 찾기 (페이지 변경 가능)
        twofa_input = page.locator("input[name='code'], input[name='passcode'], input[placeholder*='인증'], input[placeholder*='verif'], input[maxlength='4'], input[maxlength='6']")
        if twofa_input.count() > 0:
            twofa_input.first.click()
            twofa_input.first.fill("")
            twofa_input.first.type(code, delay=80)
            time.sleep(1)

            # 확인 버튼
            confirm_btn = page.locator("button.submit, button[type='submit'], button:has-text('확인'), button:has-text('Confirm')")
            if confirm_btn.count() > 0:
                confirm_btn.first.click()
                time.sleep(5)

    # 4. 로그인 성공 확인
    page.goto("https://www.tistory.com", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    final_url = page.url
    final_html = page.content()

    # 로그인 성공 판단: 마이페이지/관리 링크, 또는 로그인 버튼 없음
    if "로그인" in final_html[:5000] and "로그아웃" not in final_html[:5000]:
        _set_session(account_id, {"status": "failed", "message": "로그인 실패. 아이디/비밀번호를 확인해주세요."})
        return

    # 쿠키를 JSON 파일로도 저장
    _save_cookies_to_file(page, account_id)

    _set_session(account_id, {"status": "success", "message": "로그인 성공! 쿠키가 브라우저와 파일에 저장되었습니다."})
    logger.info(f"[tistory_login] {account_id} 로그인 성공")


def _save_cookies_to_file(page, account_id: str):
    """CDP 브라우저의 쿠키를 JSON 파일로 저장."""
    try:
        context = page.context
        cookies = context.cookies()
        # 티스토리/카카오 관련 쿠키만 필터링
        tistory_cookies = [
            c for c in cookies
            if any(d in (c.get("domain", "") or "") for d in [".tistory.com", ".kakao.com", "tistory.com", "kakao.com"])
        ]
        if not tistory_cookies:
            tistory_cookies = cookies  # 필터 결과가 없으면 전체 저장

        app_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(app_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        cookie_path = os.path.join(data_dir, f"tistory_cookies_{account_id}.json")
        with open(cookie_path, "w", encoding="utf-8") as f:
            json.dump(tistory_cookies, f, ensure_ascii=False, indent=2)
        logger.info(f"[tistory_login] 쿠키 파일 저장: {cookie_path} ({len(tistory_cookies)}개)")
    except Exception as e:
        logger.error(f"[tistory_login] 쿠키 파일 저장 실패: {e}")


def _wait_for_2fa(account_id: str, timeout: int = 120) -> str | None:
    """2FA 코드가 입력될 때까지 대기."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = _get_session(account_id)
        if s.get("status") == "submitting_2fa" and s.get("2fa_code"):
            return s["2fa_code"]
        if s.get("status") in ("failed", "idle"):
            return None
        time.sleep(1)
    return None
