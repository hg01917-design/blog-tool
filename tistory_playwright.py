"""
티스토리 블로그 Playwright 자동 발행 모듈.
- 쿠키 저장/로드 기반 로그인 (카카오 OAuth)
- 티스토리 에디터 HTML 모드 자동 입력 (제목, 본문 HTML, 태그)
- 임시저장 (발행 아님)
"""

import json
import os
import time
import random
import logging

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 모듈 레벨 rate limit
_last_publish_time = 0
PUBLISH_INTERVAL = 30  # 초


def _get_cookie_path(blog_id: str) -> str:
    return os.path.join(_APP_DIR, "data", f"tistory_cookies_{blog_id}.json")


def _get_blog_ids() -> list[str]:
    """환경변수 TISTORY_BLOGS에서 블로그 ID 목록을 반환합니다."""
    raw = os.environ.get("TISTORY_BLOGS", "")
    if not raw:
        return []
    return [b.strip() for b in raw.split(",") if b.strip()]


def cookies_exist(blog_id: str) -> bool:
    """쿠키 파일이 존재하는지 확인."""
    path = _get_cookie_path(blog_id)
    return os.path.exists(path) and os.path.getsize(path) > 10


def login_and_save_cookies(blog_id: str, timeout_sec: int = 120) -> dict:
    """브라우저를 열어 카카오 계정으로 수동 로그인 후 쿠키를 저장합니다.

    headless=False로 브라우저를 열고, 사용자가 로그인할 때까지 대기합니다.
    로그인 완료 후 쿠키를 JSON 파일로 저장합니다.

    Returns:
        {"success": True} 또는 {"success": False, "error": "..."}
    """
    cookie_path = _get_cookie_path(blog_id)
    os.makedirs(os.path.dirname(cookie_path), exist_ok=True)

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
            # 티스토리 로그인 페이지로 이동
            page.goto("https://www.tistory.com/auth/login", wait_until="domcontentloaded")
            logger.info("티스토리 로그인 페이지 열림. 카카오 계정으로 수동 로그인을 진행하세요.")

            # 로그인 완료 대기: tistory.com 쿠키 중 로그인 세션 쿠키 감지
            deadline = time.time() + timeout_sec
            logged_in = False
            while time.time() < deadline:
                cookies = context.cookies()
                cookie_names = {c["name"] for c in cookies}
                # 티스토리 로그인 완료 시 TSSESSION 또는 TSESSION 쿠키가 설정됨
                if "TSSESSION" in cookie_names or "TSESSION" in cookie_names:
                    logged_in = True
                    break
                # URL이 tistory.com 메인으로 리디렉트되었는지도 확인
                if "tistory.com" in page.url and "/auth/login" not in page.url:
                    logged_in = True
                    break
                time.sleep(1)

            if not logged_in:
                return {"success": False, "error": f"{timeout_sec}초 내에 로그인이 완료되지 않았습니다."}

            # 블로그 관리 페이지 방문하여 블로그 관련 쿠키도 수집
            page.goto(f"https://{blog_id}.tistory.com/manage", wait_until="domcontentloaded")
            time.sleep(2)

            # 쿠키 저장
            all_cookies = context.cookies()
            with open(cookie_path, "w", encoding="utf-8") as f:
                json.dump(all_cookies, f, ensure_ascii=False, indent=2)

            logger.info(f"쿠키 저장 완료: {cookie_path} ({len(all_cookies)}개)")
            return {"success": True, "cookie_count": len(all_cookies)}

        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            browser.close()


def upload_cookies(blog_id: str, cookies_json: str) -> dict:
    """JSON 문자열로 쿠키를 업로드하여 저장합니다.
    서버가 headless 환경일 때 로컬에서 추출한 쿠키를 업로드하는 용도.
    """
    cookie_path = _get_cookie_path(blog_id)
    os.makedirs(os.path.dirname(cookie_path), exist_ok=True)

    try:
        cookies = json.loads(cookies_json)
        if not isinstance(cookies, list):
            return {"success": False, "error": "쿠키는 JSON 배열이어야 합니다."}

        with open(cookie_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)

        return {"success": True, "cookie_count": len(cookies)}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON 파싱 실패: {e}"}


def publish_to_tistory(blog_id: str, title: str, body_html: str, tags: list[str]) -> dict:
    """티스토리 블로그에 글을 임시저장합니다.

    발행 순서:
    1. 쿠키 로드
    2. 글쓰기 페이지 이동
    3. 로그인 확인
    4. HTML 모드 전환
    5. 제목 입력
    6. 본문 HTML 붙여넣기
    7. 태그 입력
    8. 임시저장

    Args:
        blog_id: 티스토리 블로그 ID (예: "goodisak")
        title: 글 제목
        body_html: HTML 본문 (adsense 코드 포함)
        tags: 태그 리스트
    """
    global _last_publish_time

    if not blog_id:
        return {"success": False, "error": "blog_id가 지정되지 않았습니다.", "steps": []}

    if not cookies_exist(blog_id):
        return {"success": False, "error": "티스토리 쿠키가 없습니다. 먼저 로그인해주세요.", "steps": []}

    # Rate limit
    now = time.time()
    elapsed = now - _last_publish_time
    if _last_publish_time > 0 and elapsed < PUBLISH_INTERVAL:
        wait = int(PUBLISH_INTERVAL - elapsed)
        return {"success": False, "error": f"발행 간격 제한: {wait}초 후 다시 시도해주세요.", "steps": []}

    steps = []
    cookie_path = _get_cookie_path(blog_id)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        page = None
        try:
            # 1) 쿠키 로드
            with open(cookie_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            context.add_cookies(cookies)
            steps.append({"step": "쿠키 로드", "status": "success"})

            page = context.new_page()

            # 2) 글쓰기 페이지 이동
            write_url = f"https://{blog_id}.tistory.com/manage/newpost"
            page.goto(write_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # 3) 로그인 확인 (카카오 로그인 리디렉트 감지)
            current_url = page.url
            if ("accounts.kakao.com" in current_url
                    or "tistory.com/auth/login" in current_url
                    or "login" in current_url.lower()):
                steps.append({"step": "로그인 확인", "status": "failed"})
                _save_error_screenshot(page, prefix="tistory_login_expired")
                return {"success": False, "error": "쿠키가 만료되었습니다.", "steps": steps}
            steps.append({"step": "로그인 확인", "status": "success"})

            # 4) 에디터 로드 대기
            _wait_for_editor(page)
            steps.append({"step": "에디터 로드", "status": "success"})

            # 5) HTML 모드로 전환
            _switch_to_html_mode(page)
            steps.append({"step": "HTML 모드 전환", "status": "success"})
            time.sleep(random.uniform(1.0, 3.0))

            # 6) 제목 입력
            _type_title(page, title)
            steps.append({"step": "제목 입력", "status": "success"})
            time.sleep(random.uniform(1.0, 3.0))

            # 7) 본문 HTML 입력
            _type_body_html(page, body_html)
            steps.append({"step": "본문 입력", "status": "success"})
            time.sleep(random.uniform(1.0, 3.0))

            # 8) 태그 입력
            if tags:
                try:
                    _type_tags(page, tags)
                    steps.append({"step": "태그 입력", "status": "success", "count": len(tags)})
                except Exception as e:
                    logger.warning(f"태그 입력 실패: {e}")
                    steps.append({"step": "태그 입력", "status": "failed", "error": str(e)})

            time.sleep(random.uniform(1.0, 3.0))

            # 9) 임시저장
            _click_draft_save(page)
            time.sleep(3)

            post_url = page.url
            _last_publish_time = time.time()
            steps.append({"step": "임시저장", "status": "success", "url": post_url})

            return {"success": True, "post_url": post_url, "steps": steps}

        except PlaywrightTimeout as e:
            if page:
                _save_error_screenshot(page, prefix="tistory_timeout")
            steps.append({"step": "타임아웃", "status": "failed", "error": str(e)})
            return {"success": False, "error": f"타임아웃: {e}", "steps": steps}

        except Exception as e:
            if page:
                _save_error_screenshot(page, prefix="tistory_error")
            steps.append({"step": "오류", "status": "failed", "error": str(e)})
            return {"success": False, "error": str(e), "steps": steps}

        finally:
            browser.close()


# ─── 내부 헬퍼 함수들 ───


def _wait_for_editor(page):
    """티스토리 에디터가 로드될 때까지 대기합니다."""
    selectors = [
        "#post-title-inp",
        ".tit_post input",
        "#editor-root",
        ".editor_wrap",
        "#tinymce",
    ]
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=10000)
            logger.info(f"에디터 감지: {sel}")
            return
        except PlaywrightTimeout:
            continue

    # 마지막 시도: 페이지가 관리 페이지인지 확인
    time.sleep(3)
    if "/manage" in page.url:
        logger.info("관리 페이지 감지, 에디터 로드 간주")
        return

    raise PlaywrightTimeout("에디터를 찾을 수 없습니다.")


def _switch_to_html_mode(page):
    """에디터를 HTML 모드로 전환합니다."""
    # 여러 셀렉터 시도
    html_btn_selectors = [
        ".btn_html",
        "button:has-text('HTML')",
        '[data-mode="html"]',
        '.btn_mode:has-text("HTML")',
        '#mceu_18 button',  # TinyMCE HTML 버튼
    ]

    for sel in html_btn_selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(1)
                logger.info(f"HTML 모드 전환 성공: {sel}")
                return
        except Exception:
            continue

    # JavaScript로 HTML 모드 버튼 탐색
    try:
        clicked = page.evaluate("""() => {
            // 'HTML' 텍스트가 포함된 버튼 찾기
            const buttons = document.querySelectorAll('button, a, span');
            for (const btn of buttons) {
                if (btn.textContent.trim() === 'HTML' || btn.textContent.trim() === 'html') {
                    btn.click();
                    return true;
                }
            }
            // 모드 전환 탭에서 HTML 찾기
            const tabs = document.querySelectorAll('[class*="mode"], [class*="tab"]');
            for (const tab of tabs) {
                if (tab.textContent.includes('HTML')) {
                    tab.click();
                    return true;
                }
            }
            return false;
        }""")
        if clicked:
            time.sleep(1)
            logger.info("HTML 모드 전환 성공 (JS)")
            return
    except Exception:
        pass

    logger.warning("HTML 모드 버튼을 찾지 못했습니다. 기본 모드로 진행합니다.")


def _type_title(page, title: str):
    """제목을 입력합니다."""
    title_selectors = [
        "#post-title-inp",
        ".tit_post input",
        'input[placeholder*="제목"]',
        ".title_input input",
        "#title",
    ]

    for sel in title_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                time.sleep(random.uniform(1.0, 3.0))
                el.type(title, delay=random.randint(40, 120))
                time.sleep(random.uniform(1.0, 3.0))
                logger.info(f"제목 입력 성공: {sel}")
                return
        except Exception:
            continue

    # JavaScript로 제목 입력 시도
    try:
        page.evaluate(f"""() => {{
            const inp = document.querySelector('#post-title-inp')
                || document.querySelector('.tit_post input')
                || document.querySelector('input[placeholder*="제목"]');
            if (inp) {{
                inp.value = {json.dumps(title)};
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                return true;
            }}
            return false;
        }}""")
        logger.info("제목 입력 성공 (JS)")
        return
    except Exception:
        pass

    raise RuntimeError("제목 입력 필드를 찾을 수 없습니다.")


def _type_body_html(page, body_html: str):
    """HTML 본문을 입력합니다."""
    # HTML 모드 textarea/CodeMirror에 입력
    html_editor_selectors = [
        "#html-editor-area",
        ".CodeMirror",
        "textarea.html",
        "#editor-textarea",
        ".editor_body textarea",
    ]

    # 1) CodeMirror 에디터 시도
    try:
        cm = page.query_selector(".CodeMirror")
        if cm and cm.is_visible():
            page.evaluate("""(html) => {
                const cm = document.querySelector('.CodeMirror');
                if (cm && cm.CodeMirror) {
                    cm.CodeMirror.setValue(html);
                    return true;
                }
                return false;
            }""", body_html)
            time.sleep(0.5)
            logger.info("본문 입력 성공 (CodeMirror)")
            return
    except Exception:
        pass

    # 2) textarea 시도
    for sel in html_editor_selectors:
        if sel == ".CodeMirror":
            continue
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.type(body_html, delay=random.randint(40, 120))
                time.sleep(random.uniform(1.0, 3.0))
                logger.info(f"본문 입력 성공: {sel}")
                return
        except Exception:
            continue

    # 3) contenteditable 영역에 innerHTML 설정 (WYSIWYG 모드 fallback)
    try:
        result = page.evaluate("""(html) => {
            // iframe 내부 에디터
            const iframe = document.querySelector('#editor-iframe, iframe.editor');
            if (iframe && iframe.contentDocument) {
                const body = iframe.contentDocument.body;
                if (body) {
                    body.innerHTML = html;
                    return 'iframe';
                }
            }
            // contenteditable div
            const editables = document.querySelectorAll('[contenteditable="true"]');
            for (const el of editables) {
                if (el.offsetHeight > 100) {
                    el.innerHTML = html;
                    return 'contenteditable';
                }
            }
            // TinyMCE
            if (window.tinymce && tinymce.activeEditor) {
                tinymce.activeEditor.setContent(html);
                return 'tinymce';
            }
            return null;
        }""", body_html)

        if result:
            time.sleep(0.5)
            logger.info(f"본문 입력 성공 (JS fallback: {result})")
            return
    except Exception:
        pass

    raise RuntimeError("HTML 에디터 영역을 찾을 수 없습니다.")


def _type_tags(page, tags: list[str]):
    """태그를 입력합니다."""
    tag_selectors = [
        "#tagText",
        ".tag_post input",
        'input[placeholder*="태그"]',
        'input[placeholder*="Tag"]',
        ".tag_input input",
    ]

    tag_input = None
    for sel in tag_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                tag_input = el
                logger.info(f"태그 입력 필드 감지: {sel}")
                break
        except Exception:
            continue

    if not tag_input:
        # JavaScript로 태그 입력 필드 찾기
        try:
            found = page.evaluate("""() => {
                const inp = document.querySelector('#tagText')
                    || document.querySelector('.tag_post input')
                    || document.querySelector('input[placeholder*="태그"]');
                if (inp) {
                    inp.scrollIntoView();
                    inp.focus();
                    return true;
                }
                return false;
            }""")
            if found:
                tag_input = page.query_selector("#tagText") or page.query_selector(".tag_post input")
        except Exception:
            pass

    if not tag_input:
        raise RuntimeError("태그 입력 필드를 찾을 수 없습니다.")

    tag_input.click()
    time.sleep(0.3)

    for tag in tags:
        tag_input.fill("")
        tag_input.type(tag.strip(), delay=random.randint(40, 120))
        page.keyboard.press("Enter")
        time.sleep(random.uniform(1.0, 3.0))


def _click_draft_save(page):
    """임시저장 버튼을 클릭합니다."""
    # 여러 셀렉터 시도
    draft_selectors = [
        'button:has-text("임시저장")',
        '.btn_save:has-text("임시저장")',
        '#save-btn',
    ]

    for sel in draft_selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                logger.info(f"임시저장 클릭 성공: {sel}")
                return
        except Exception:
            continue

    # JavaScript로 임시저장 버튼 찾기
    try:
        clicked = page.evaluate("""() => {
            const buttons = document.querySelectorAll('button, a, input[type="button"]');
            for (const btn of buttons) {
                const text = btn.textContent.trim();
                if (text === '임시저장' || text.includes('임시저장')) {
                    btn.click();
                    return true;
                }
            }
            // 저장 버튼 찾기
            for (const btn of buttons) {
                const text = btn.textContent.trim();
                if (text === '저장' && !text.includes('발행')) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }""")
        if clicked:
            logger.info("임시저장 클릭 성공 (JS)")
            return
    except Exception:
        pass

    raise RuntimeError("임시저장 버튼을 찾을 수 없습니다.")


def _save_error_screenshot(page, prefix="tistory_error"):
    """에러 발생 시 스크린샷 저장."""
    if page is None:
        return
    try:
        screenshot_dir = os.path.join(_APP_DIR, "data")
        os.makedirs(screenshot_dir, exist_ok=True)
        ts = int(time.time())
        path = os.path.join(screenshot_dir, f"{prefix}_{ts}.png")
        page.screenshot(path=path)
        logger.info(f"에러 스크린샷 저장: {path}")
    except Exception:
        pass
