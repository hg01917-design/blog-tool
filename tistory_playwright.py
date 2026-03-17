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


def publish_to_tistory(blog_id: str, title: str, body_html: str, tags: list[str],
                       account_id: str = None) -> dict:
    """티스토리 블로그에 글을 임시저장합니다.

    Args:
        blog_id: 티스토리 블로그 ID 또는 커스텀 도메인 (글쓰기 URL용)
        title: 글 제목
        body_html: HTML 본문 (adsense 코드 포함)
        tags: 태그 리스트
        account_id: 쿠키 저장에 사용된 계정 ID (없으면 blog_id 사용)
    """
    global _last_publish_time

    if not blog_id:
        return {"success": False, "error": "blog_id가 지정되지 않았습니다.", "steps": []}

    # 쿠키는 account_id 기준으로 저장됨
    cookie_id = account_id or blog_id
    if not cookies_exist(cookie_id):
        return {"success": False, "error": "티스토리 쿠키가 없습니다. 먼저 로그인해주세요.", "steps": []}

    # Rate limit
    now = time.time()
    elapsed = now - _last_publish_time
    if _last_publish_time > 0 and elapsed < PUBLISH_INTERVAL:
        wait = int(PUBLISH_INTERVAL - elapsed)
        return {"success": False, "error": f"발행 간격 제한: {wait}초 후 다시 시도해주세요.", "steps": []}

    steps = []
    cookie_path = _get_cookie_path(cookie_id)

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
            # 커스텀 도메인이면 그대로 사용, 아니면 .tistory.com 붙임
            if "." in blog_id and not blog_id.endswith(".tistory.com"):
                write_url = f"https://{blog_id}/manage/newpost"
            else:
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

            # 3-1) 임시저장 복원 팝업 자동 닫기
            _dismiss_restore_popup(page)

            # 4) 에디터 로드 대기
            _wait_for_editor(page)
            steps.append({"step": "에디터 로드", "status": "success"})

            # 5) 제목 입력
            _type_title(page, title)
            steps.append({"step": "제목 입력", "status": "success"})
            time.sleep(random.uniform(1.0, 3.0))

            # 6) 본문 HTML 입력 (TinyMCE)
            _type_body_html(page, body_html)
            steps.append({"step": "본문 입력", "status": "success"})
            time.sleep(random.uniform(1.0, 3.0))

            # 7) 태그 입력
            if tags:
                try:
                    _type_tags(page, tags)
                    steps.append({"step": "태그 입력", "status": "success", "count": len(tags)})
                except Exception as e:
                    logger.warning(f"태그 입력 실패: {e}")
                    steps.append({"step": "태그 입력", "status": "failed", "error": str(e)})

            time.sleep(random.uniform(1.0, 3.0))

            # 8) 임시저장
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


def _dismiss_restore_popup(page):
    """임시저장 복원 팝업이 뜨면 '새 글 작성' 버튼 클릭으로 닫습니다."""
    try:
        time.sleep(1)
        dismissed = page.evaluate("""() => {
            // '새 글 작성' 또는 '새로 작성' 버튼 찾기
            const buttons = document.querySelectorAll('button, a');
            for (const btn of buttons) {
                const text = btn.textContent.trim();
                if (text.includes('새 글 작성') || text.includes('새로 작성')
                    || text.includes('취소') || text.includes('아니오')) {
                    btn.click();
                    return text;
                }
            }
            // 모달 닫기 버튼
            const closeBtn = document.querySelector('.btn_cancel, .btn-close, [class*="close"]');
            if (closeBtn) {
                closeBtn.click();
                return 'close_btn';
            }
            return null;
        }""")
        if dismissed:
            logger.info(f"임시저장 복원 팝업 닫기: {dismissed}")
            time.sleep(1)
    except Exception:
        pass


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
    """TinyMCE iframe 안에서 타이핑 방식으로 본문을 입력합니다.
    ##H2:소제목##, ##H3:소제목##, ##AD## 마크업을 인식하여 처리합니다."""
    import re as _re

    # TinyMCE iframe이 로드될 때까지 대기
    try:
        page.wait_for_selector("#editor-tistory_ifr", timeout=15000)
    except PlaywrightTimeout:
        raise RuntimeError("TinyMCE 에디터 iframe을 찾을 수 없습니다.")

    # iframe 내부로 진입
    iframe_el = page.query_selector("#editor-tistory_ifr")
    if not iframe_el:
        raise RuntimeError("TinyMCE iframe 요소를 찾을 수 없습니다.")

    frame = iframe_el.content_frame()
    if not frame:
        raise RuntimeError("TinyMCE iframe 내부에 접근할 수 없습니다.")

    # body[contenteditable] 클릭
    body_el = frame.query_selector("body")
    if not body_el:
        raise RuntimeError("TinyMCE body 요소를 찾을 수 없습니다.")
    body_el.click()
    time.sleep(0.5)

    # HTML → 마크업 보존 평문 변환
    text = body_html
    # ##H2/H3/AD## 마크업 보존, HTML 태그에서 H2/H3도 마크업으로 변환
    text = _re.sub(r'<h2[^>]*>(.*?)</h2>', r'##H2:\1##', text, flags=_re.DOTALL)
    text = _re.sub(r'<h3[^>]*>(.*?)</h3>', r'##H3:\1##', text, flags=_re.DOTALL)
    text = _re.sub(r'<br\s*/?>', '\n', text)
    text = _re.sub(r'</(?:p|div|li|tr)>', '\n', text)
    text = _re.sub(r'<[^>]+>', '', text)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    # 줄 단위로 처리
    lines = text.split('\n')
    for line in lines:
        stripped = line.strip()
        if not stripped:
            frame.page.keyboard.press("Enter")
            time.sleep(0.05)
            continue

        # ##H2:소제목## 처리
        h2_match = _re.match(r'##H2:(.+?)##', stripped)
        if h2_match:
            heading_text = h2_match.group(1).strip()
            # TinyMCE 서식 변경: H2
            page.evaluate("() => { if(tinymce.activeEditor) tinymce.activeEditor.execCommand('FormatBlock', false, 'h2'); }")
            time.sleep(0.3)
            frame.page.keyboard.type(heading_text, delay=random.randint(40, 120))
            frame.page.keyboard.press("Enter")
            time.sleep(0.3)
            # 본문(p)으로 복귀
            page.evaluate("() => { if(tinymce.activeEditor) tinymce.activeEditor.execCommand('FormatBlock', false, 'p'); }")
            time.sleep(0.3)
            continue

        # ##H3:소제목## 처리
        h3_match = _re.match(r'##H3:(.+?)##', stripped)
        if h3_match:
            heading_text = h3_match.group(1).strip()
            page.evaluate("() => { if(tinymce.activeEditor) tinymce.activeEditor.execCommand('FormatBlock', false, 'h3'); }")
            time.sleep(0.3)
            frame.page.keyboard.type(heading_text, delay=random.randint(40, 120))
            frame.page.keyboard.press("Enter")
            time.sleep(0.3)
            page.evaluate("() => { if(tinymce.activeEditor) tinymce.activeEditor.execCommand('FormatBlock', false, 'p'); }")
            time.sleep(0.3)
            continue

        # ##AD## 처리: ins 태그 직접 삽입 (script 태그 금지)
        if stripped == '##AD##':
            try:
                ad_html = (
                    '<ins class="adsbygoogle" '
                    'style="display:block;text-align:center;" '
                    'data-ad-layout="in-article" '
                    'data-ad-format="fluid" '
                    'data-ad-client="ca-pub-1646757278810260" '
                    'data-ad-slot="3141593954"></ins>'
                )
                page.evaluate("""(html) => {
                    if (tinymce.activeEditor) {
                        tinymce.activeEditor.execCommand('insertHTML', false, html);
                    }
                }""", ad_html)
                time.sleep(0.5)
                logger.info("애드센스 ins 태그 삽입 완료")
            except Exception as e:
                logger.warning(f"애드센스 삽입 실패: {e}")
            continue

        # 일반 텍스트 타이핑
        frame.page.keyboard.type(stripped, delay=random.randint(40, 120))
        frame.page.keyboard.press("Enter")
        time.sleep(random.uniform(0.1, 0.3))

    # TinyMCE에 변경사항 반영
    page.evaluate("""() => {
        if (window.tinymce && tinymce.activeEditor) {
            tinymce.activeEditor.fire('change');
            tinymce.activeEditor.save();
        }
    }""")
    time.sleep(0.5)
    logger.info("본문 타이핑 입력 완료 (TinyMCE iframe)")


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
