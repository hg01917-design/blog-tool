"""
티스토리 블로그 Playwright 자동 발행 모듈.
- 쿠키 저장/로드 기반 로그인 (카카오 OAuth)
- 티스토리 에디터 HTML 모드 자동 입력 (제목, 본문 HTML, 태그)
- 임시저장 (발행 아님)
"""

import json
import os
import re as _re_module
import time
import random
import logging
import base64
import tempfile

import requests as http_requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 모듈 레벨 rate limit
_last_publish_time = 0
PUBLISH_INTERVAL = 30  # 초


CDP_URL = "http://127.0.0.1:9222"


def _is_local_mode() -> bool:
    """LOCAL_MODE 환경변수가 true인지 확인합니다."""
    return os.environ.get("LOCAL_MODE", "").lower() == "true"


def _get_local_chrome_profile() -> str:
    """로컬 크롬 프로필 경로를 자동 감지합니다."""
    import platform
    system = platform.system()
    if system == "Windows":
        username = os.environ.get("USERNAME", os.environ.get("USER", ""))
        path = f"C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\User Data"
    elif system == "Darwin":
        path = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    else:  # Linux
        path = os.path.expanduser("~/.config/google-chrome")
    if os.path.isdir(path):
        return path
    raise RuntimeError(f"로컬 크롬 프로필을 찾을 수 없습니다: {path}")


def _get_blog_ids() -> list[str]:
    """환경변수 TISTORY_BLOGS에서 블로그 ID 목록을 반환합니다."""
    raw = os.environ.get("TISTORY_BLOGS", "")
    if not raw:
        return []
    return [b.strip() for b in raw.split(",") if b.strip()]


def _connect_browser(p):
    """브라우저에 연결합니다. LOCAL_MODE면 로컬 크롬 프로필 사용."""
    if _is_local_mode():
        return None  # 로컬 모드에서는 persistent context 사용
    try:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        return browser
    except Exception as e:
        raise RuntimeError(
            f"브라우저 데몬에 연결할 수 없습니다 ({CDP_URL}). "
            f"browser_daemon.py가 실행 중인지 확인하세요: {e}"
        )


def _open_local_context(p):
    """로컬 크롬 프로필로 persistent context를 엽니다."""
    chrome_profile = _get_local_chrome_profile()
    context = p.chromium.launch_persistent_context(
        user_data_dir=chrome_profile,
        headless=False,
        channel="chrome",
        viewport={"width": 1280, "height": 900},
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    return context


def _get_context_and_page(browser, p=None):
    """브라우저에서 context와 새 페이지를 가져옵니다.
    LOCAL_MODE면 로컬 크롬 persistent context를 사용합니다."""
    if _is_local_mode() and p:
        context = _open_local_context(p)
        page = context.pages[0] if context.pages else context.new_page()
        return context, page
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    return context, page


def cookies_exist(blog_id: str) -> bool:
    """로그인 상태 확인. LOCAL_MODE면 로컬 크롬 프로필 존재 여부 확인."""
    if _is_local_mode():
        try:
            _get_local_chrome_profile()
            return True
        except RuntimeError:
            return False
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=3)
        return resp.status == 200
    except Exception:
        return False


def login_and_save_cookies(blog_id: str, timeout_sec: int = 120) -> dict:
    """상시 실행 브라우저에서 카카오 로그인을 수행합니다.
    server_login.py를 사용하세요.
    """
    return {"success": False, "error": "server_login.py를 사용해 로그인하세요."}


def upload_cookies(blog_id: str, cookies_json: str) -> dict:
    """CDP 브라우저에 쿠키를 주입합니다."""
    try:
        cookies = json.loads(cookies_json)
        if not isinstance(cookies, list):
            return {"success": False, "error": "쿠키는 JSON 배열이어야 합니다."}

        with sync_playwright() as p:
            browser = _connect_browser(p)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            try:
                context.add_cookies(cookies)
                page = context.new_page()
                page.goto("https://www.tistory.com", wait_until="domcontentloaded")
                time.sleep(2)
                page.close()
            except Exception:
                pass

        return {"success": True, "cookie_count": len(cookies)}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON 파싱 실패: {e}"}


def publish_to_tistory(blog_id: str, title: str, body_html: str, tags: list[str],
                       account_id: str = None, category: str = "it") -> dict:
    """티스토리 블로그에 글을 임시저장합니다.

    Args:
        blog_id: 티스토리 블로그 ID 또는 커스텀 도메인 (글쓰기 URL용)
        title: 글 제목
        body_html: HTML 본문 (adsense 코드 포함)
        tags: 태그 리스트
        account_id: 쿠키 저장에 사용된 계정 ID (없으면 blog_id 사용)
        category: 글 카테고리 (it, travel 등) - 썸네일 생성에 사용
    """
    global _last_publish_time

    if not blog_id:
        return {"success": False, "error": "blog_id가 지정되지 않았습니다.", "steps": []}

    # account_id 기준으로 프로파일 사용
    cookie_id = account_id or blog_id
    if not cookies_exist(cookie_id):
        return {"success": False, "error": "브라우저 프로파일이 없습니다. 먼저 로그인해주세요.", "steps": []}

    # Rate limit
    now = time.time()
    elapsed = now - _last_publish_time
    if _last_publish_time > 0 and elapsed < PUBLISH_INTERVAL:
        wait = int(PUBLISH_INTERVAL - elapsed)
        return {"success": False, "error": f"발행 간격 제한: {wait}초 후 다시 시도해주세요.", "steps": []}

    steps = []
    tmp_files = []  # 임시 이미지 파일 추적 (cleanup용)

    # ── Imagen 썸네일 생성 (제목 기반, 카테고리별 프롬프트) ──
    import re as _re
    thumbnail_path = None
    try:
        thumbnail_path = _generate_imagen_thumbnail(title, category)
        tmp_files.append(thumbnail_path)
        steps.append({"step": "Imagen 썸네일 생성", "status": "success"})
    except Exception as e:
        logger.warning(f"Imagen 썸네일 생성 실패: {e}")
        steps.append({"step": "Imagen 썸네일 생성", "status": "failed", "error": str(e)})

    # ── Unsplash 이미지: 본문에서 H2 소제목 추출 → 소제목별 이미지 검색 ──
    h2_headings = _re.findall(r'##H2:(.+?)##', body_html)
    if not h2_headings:
        h2_headings = [_re.sub(r'<[^>]+>', '', m).strip()
                       for m in _re.findall(r'<h2[^>]*>(.*?)</h2>', body_html, _re.DOTALL)]
    if not h2_headings:
        # 마크다운 ## 형식도 지원
        h2_headings = _re.findall(r'^##\s+(.+)$', body_html, _re.MULTILINE)
    section_image_urls = []
    for heading in h2_headings[:3]:  # 최대 3장
        try:
            en_query = _translate_to_english(heading)
            img_url = _fetch_unsplash_image_url(en_query)
            section_image_urls.append(img_url)
            steps.append({"step": f"Unsplash 이미지({heading[:15]})", "status": "success"})
        except Exception as e:
            logger.warning(f"Unsplash 이미지 실패 ({heading}): {e}")
            steps.append({"step": f"Unsplash 이미지({heading[:15]})", "status": "failed", "error": str(e)})

    with sync_playwright() as p:
        browser = _connect_browser(p)

        page = None
        try:
            context, page = _get_context_and_page(browser, p)
            steps.append({"step": "브라우저 연결", "status": "success"})

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

            # 6) 본문 HTML 입력 (TinyMCE) + 소제목별 이미지 삽입
            _type_body_html(page, body_html, image_urls=section_image_urls)
            steps.append({"step": "본문 입력", "status": "success",
                          "images": len(section_image_urls)})
            time.sleep(random.uniform(1.0, 3.0))

            # 6-1) Imagen 썸네일을 본문 맨 앞에 삽입 + 대표이미지 설정
            if thumbnail_path:
                try:
                    _insert_thumbnail_and_set_representative(page, thumbnail_path, blog_id)
                    steps.append({"step": "썸네일 삽입 + 대표이미지", "status": "success"})
                except Exception as e:
                    logger.warning(f"썸네일 삽입 실패: {e}")
                    steps.append({"step": "썸네일 삽입 + 대표이미지", "status": "failed", "error": str(e)})
                time.sleep(random.uniform(1.0, 2.0))

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
            # 임시 이미지 파일 정리
            for f in tmp_files:
                try:
                    if os.path.exists(f):
                        os.unlink(f)
                except Exception:
                    pass
            if page:
                try:
                    page.close()
                except Exception:
                    pass


def edit_latest_draft(blog_id: str, account_id: str = None, category: str = "it") -> dict:
    """기존 임시저장 글을 열어 base64 이미지를 Unsplash URL로 교체하고
    Imagen 썸네일을 생성/삽입하고 애드센스를 삽입한 뒤 재저장합니다.

    흐름:
      1. /manage/posts 에서 최신 임시저장 글 클릭
      2. Imagen 썸네일 생성 + 본문 맨 앞 삽입
      3. 에디터 iframe에서 base64 img → Unsplash URL 교체
      4. ##AD## 텍스트가 있으면 애드센스 DOM 삽입
      5. 대표이미지 설정
      6. 임시저장
    """
    if not cookies_exist(account_id or blog_id):
        return {"success": False, "error": "브라우저 데몬이 실행 중이 아닙니다."}

    steps = []

    base_url = (f"https://{blog_id}" if "." in blog_id
                else f"https://{blog_id}.tistory.com")

    with sync_playwright() as p:
        browser = _connect_browser(p)
        page = None
        try:
            context, page = _get_context_and_page(browser, p)

            # ── 1) 글쓰기 페이지 → 복원 팝업에서 임시저장 글 불러오기 ──
            write_url = f"{base_url}/manage/newpost"
            page.goto(write_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            if "accounts.kakao.com" in page.url or "auth/login" in page.url:
                return {"success": False, "error": "쿠키 만료", "steps": steps}
            steps.append({"step": "글쓰기 접속", "status": "success"})

            # "임시저장" 숫자(.count) 버튼 클릭 → 임시저장 목록 팝업
            _dismiss_restore_popup(page)  # 자동복원 팝업 먼저 닫기
            time.sleep(1)

            # .btn-draft 안의 .count (숫자) 클릭 → 목록 열기
            draft_count = page.evaluate("""() => {
                const countBtn = document.querySelector('.btn-draft .count, .btn_draft .count');
                if (countBtn) { countBtn.click(); return countBtn.textContent.trim(); }
                return null;
            }""")

            if not draft_count:
                _save_error_screenshot(page, prefix="edit_draft_nobtn")
                return {"success": False, "error": "임시저장 개수 버튼을 찾을 수 없습니다.", "steps": steps}

            logger.info(f"임시저장 목록 열기 (개수: {draft_count})")
            time.sleep(2)
            _save_error_screenshot(page, prefix="edit_draft_list")

            # 임시저장 목록에서 제목이 있는 첫 번째 글 클릭
            # ("제목 없음" 스킵, 실제 제목이 있는 글 선택)
            draft_loaded = page.evaluate("""() => {
                // 목록 영역의 모든 행에서 제목 텍스트 찾기
                const rows = document.querySelectorAll('li, tr, div');
                for (const row of rows) {
                    const text = row.textContent.trim();
                    // 제목이 있는 행 (방금/N분전 + 실제 제목)
                    if ((text.includes('방금') || text.includes('분전') || text.includes('시간전'))
                        && !text.startsWith('임시저장')
                        && text.length > 10) {
                        // "제목 없음" 스킵
                        if (text.includes('제목 없음') || text.includes('제목없음')) continue;
                        // 클릭 가능한 요소 찾기
                        const clickable = row.querySelector('a') || row;
                        clickable.click();
                        // 제목 부분만 추출 (방금/분전 뒤의 텍스트)
                        const title = text.replace(/^(방금|\\d+분전|\\d+시간전)\\s*/, '').substring(0, 80);
                        return title;
                    }
                }
                // fallback: 아무 글이나 첫 번째
                for (const row of rows) {
                    const text = row.textContent.trim();
                    if (text.includes('방금') || text.includes('분전')) {
                        const clickable = row.querySelector('a') || row;
                        clickable.click();
                        return text.substring(0, 80);
                    }
                }
                return null;
            }""")

            if not draft_loaded:
                _save_error_screenshot(page, prefix="edit_draft_noitem")
                return {"success": False, "error": "임시저장 목록에서 글을 선택할 수 없습니다.", "steps": steps}

            logger.info(f"임시저장 글 로드: {draft_loaded}")
            time.sleep(4)  # 글 로드 대기
            steps.append({"step": "임시저장 글 로드", "status": "success", "title": draft_loaded})

            # ── 2) 에디터 iframe 접근 ──
            try:
                page.wait_for_selector("#editor-tistory_ifr", timeout=15000)
            except PlaywrightTimeout:
                _save_error_screenshot(page, prefix="edit_draft_noeditor")
                return {"success": False, "error": "에디터 iframe 없음", "steps": steps}

            iframe_el = page.query_selector("#editor-tistory_ifr")
            frame = iframe_el.content_frame() if iframe_el else None
            if not frame:
                return {"success": False, "error": "iframe 접근 불가", "steps": steps}

            # ── 3) H2 소제목 추출 ──
            h2_texts = frame.evaluate("""() => {
                const h2s = document.querySelectorAll('h2');
                return Array.from(h2s).map(h => h.innerText.trim()).filter(t => t.length > 0);
            }""")
            logger.info(f"H2 소제목 {len(h2_texts)}개: {h2_texts}")

            # ── 3-1) Imagen 썸네일 생성 (제목 기반) ──
            thumbnail_path = None
            try:
                # draft_loaded에서 제목 추출 (또는 에디터 제목 필드에서)
                draft_title = page.evaluate("""() => {
                    const titleInput = document.querySelector('#post-title-inp');
                    return titleInput ? titleInput.value : '';
                }""") or draft_loaded or ""
                if draft_title:
                    thumbnail_path = _generate_imagen_thumbnail(draft_title, category)
                    steps.append({"step": "Imagen 썸네일 생성", "status": "success"})
            except Exception as e:
                logger.warning(f"Imagen 썸네일 생성 실패: {e}")
                steps.append({"step": "Imagen 썸네일 생성", "status": "failed", "error": str(e)})

            # ── 4) Unsplash URL 미리 준비 (base64 이미지 교체용) ──
            # TinyMCE getContent()에서 base64 이미지 개수 확인
            b64_count = page.evaluate("""() => {
                const html = tinymce.activeEditor ? tinymce.activeEditor.getContent() : '';
                return (html.match(/src="data:image/g) || []).length;
            }""")
            logger.info(f"base64 이미지 {b64_count}개 발견 (TinyMCE content)")

            unsplash_urls = []
            img_count = max(b64_count, 0)
            # 이미지가 없으면 H2 기반으로 추가할 개수
            if img_count == 0:
                total_imgs = page.evaluate("""() => {
                    const html = tinymce.activeEditor ? tinymce.activeEditor.getContent() : '';
                    return (html.match(/<img/g) || []).length;
                }""")
                if total_imgs == 0 and h2_texts:
                    img_count = min(3, len(h2_texts))

            for i in range(img_count):
                query_text = h2_texts[i] if i < len(h2_texts) else (h2_texts[0] if h2_texts else "nature landscape")
                try:
                    en_query = _translate_to_english(query_text)
                    url = _fetch_unsplash_image_url(en_query)
                    unsplash_urls.append(url)
                    steps.append({"step": f"Unsplash({query_text[:15]})", "status": "success"})
                except Exception as e:
                    logger.warning(f"Unsplash 실패 ({query_text}): {e}")
                    unsplash_urls.append("")
                    steps.append({"step": f"Unsplash({query_text[:15]})", "status": "failed", "error": str(e)})

            # ── 5) TinyMCE getContent → Python 가공 → setContent ──
            raw_html = page.evaluate("""() => {
                const editor = tinymce.activeEditor;
                return editor ? editor.getContent() : '';
            }""")

            result_info = {}
            if not raw_html:
                steps.append({"step": "HTML 추출 실패", "status": "failed"})
            else:
                html = raw_html
                replaced_count = 0

                # 5-1) base64 이미지를 Unsplash URL로 교체
                for url in unsplash_urls:
                    if not url:
                        continue
                    m = _re_module.search(r'src="data:image[^"]*"', html)
                    if m:
                        html = html[:m.start()] + f'src="{url}" style="max-width:100%;height:auto;border-radius:8px;"' + html[m.end():]
                        replaced_count += 1

                # 5-2) 마크다운 표 → HTML table 변환
                table_converted = 0
                # TinyMCE에서 마크다운 표가 <p>|...|</p> 형태로 저장됨
                def _convert_md_table_in_html(match_html):
                    nonlocal table_converted
                    block = match_html.group(0)
                    # <p> 태그 제거하고 줄 추출
                    lines = _re_module.findall(r'(\|[^<]+\|)', block)
                    if len(lines) < 2:
                        return block
                    table_html = _markdown_table_to_html(lines)
                    if table_html:
                        table_converted += 1
                        return table_html
                    return block

                # 연속된 <p>|...|</p> 블록 찾기 (표 행들)
                html = _re_module.sub(
                    r'(?:<p>\s*\|[^<]*\|\s*</p>\s*){2,}',
                    _convert_md_table_in_html,
                    html,
                )
                if table_converted:
                    logger.info(f"마크다운 표 {table_converted}개 HTML 변환")

                # 5-3) 애드센스 흔적 제거
                for pattern in [
                    r'<script[^>]*pagead2[^>]*>[\s\S]*?</script>',
                    r'<script[^>]*>[^<]*adsbygoogle[^<]*</script>',
                    r'<ins[^>]*adsbygoogle[^>]*>[\s\S]*?</ins>',
                    r'<div[^>]*ad-container[^>]*>[\s\S]*?</div>',
                    r'<div[^>]*ad-adsense[^>]*>[\s\S]*?</div>',
                    r'\(adsbygoogle\s*=\s*window\.adsbygoogle\s*\|\|\s*\[\]\)\.push\(\{\}\);?',
                ]:
                    html = _re_module.sub(pattern, '', html, flags=_re_module.IGNORECASE)
                html = _re_module.sub(r'##AD##', '', html)
                html = _re_module.sub(r'(<p>\s*(&nbsp;)?\s*</p>\s*){3,}', '<p>&nbsp;</p>', html, flags=_re_module.IGNORECASE)

                # 5-4) 애드센스 삽입 (2번째, 4번째 H2 뒤 첫 번째 </p> 이후)
                ad_block = (
                    '<div class="ad-adsense" style="margin:1.5em 0;text-align:center;">'
                    '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1646757278810260" crossorigin="anonymous"></script>'
                    '<ins class="adsbygoogle" style="display:block;text-align:center;" data-ad-layout="in-article" data-ad-format="fluid" data-ad-client="ca-pub-1646757278810260" data-ad-slot="3141593954"></ins>'
                    '<script>(adsbygoogle = window.adsbygoogle || []).push({});</script>'
                    '</div>'
                )

                h2_positions = [m.end() for m in _re_module.finditer(r'<h2[^>]*>[\s\S]*?</h2>', html, _re_module.IGNORECASE)]
                ad_count = 0
                # 역순 삽입 (4번째 → 2번째, 0-indexed: 3 → 1)
                for idx in [3, 1]:
                    if idx >= len(h2_positions):
                        continue
                    pos = h2_positions[idx]
                    # H2 뒤 첫 </p> 찾되, <img 직후가 아닌 텍스트 </p> 뒤에 삽입
                    search_start = pos
                    while True:
                        p_close = html.find('</p>', search_start)
                        if p_close < 0:
                            break
                        insert_pos = p_close + 4
                        # 이 </p> 바로 앞에 img 태그가 있으면 건너뛰기
                        p_open = html.rfind('<p', search_start, p_close)
                        p_content = html[p_open:p_close] if p_open >= 0 else ''
                        if _re_module.search(r'<img\s', p_content):
                            search_start = insert_pos
                            continue
                        # img 없는 텍스트 블록 뒤 → 여기에 삽입
                        html = html[:insert_pos] + ad_block + html[insert_pos:]
                        ad_count += 1
                        break

                # 5-5) Imagen 썸네일을 본문 맨 앞에 삽입
                thumb_inserted = False
                if thumbnail_path:
                    try:
                        with open(thumbnail_path, "rb") as f:
                            thumb_bytes = f.read()
                        thumb_b64 = base64.b64encode(thumb_bytes).decode("ascii")
                        thumb_html = (
                            '<p style="text-align:center;">'
                            f'<img src="data:image/webp;base64,{thumb_b64}" '
                            'style="max-width:100%;height:auto;border-radius:8px;" '
                            'alt="썸네일" />'
                            '</p><p>&nbsp;</p>'
                        )
                        html = thumb_html + html
                        thumb_inserted = True
                        logger.info("Imagen 썸네일 HTML 맨 앞 삽입")
                    except Exception as e:
                        logger.warning(f"썸네일 HTML 삽입 실패: {e}")

                # setContent
                page.evaluate("(h) => { tinymce.activeEditor.setContent(h); }", html)

                result_info = {"replaced": replaced_count, "ads": ad_count, "tables": table_converted,
                               "thumbnail": thumb_inserted}
                logger.info(f"이미지 교체 {replaced_count}개, 애드센스 삽입 {ad_count}개, 표 변환 {table_converted}개, 썸네일 {thumb_inserted}")
                steps.append({"step": "이미지 교체 + 애드센스 + 표", "status": "success",
                              "replaced": replaced_count, "ads": ad_count, "tables": table_converted})

            # TinyMCE 변경 알림
            page.evaluate("""() => {
                if (window.tinymce && tinymce.activeEditor) {
                    tinymce.activeEditor.fire('change');
                    tinymce.activeEditor.save();
                }
            }""")
            time.sleep(1)

            # ── 5-6) 대표이미지 설정 ──
            if thumbnail_path:
                try:
                    _set_tistory_representative_image(page)
                    steps.append({"step": "대표이미지 설정", "status": "success"})
                except Exception as e:
                    logger.warning(f"대표이미지 설정 실패: {e}")
                    steps.append({"step": "대표이미지 설정", "status": "failed", "error": str(e)})
                # 임시 파일 정리
                try:
                    if os.path.exists(thumbnail_path):
                        os.unlink(thumbnail_path)
                except Exception:
                    pass

            # ── 6) 임시저장 ──
            _click_draft_save(page)
            time.sleep(3)
            steps.append({"step": "임시저장", "status": "success"})

            # 최종 상태 확인
            final_b64 = frame.evaluate("() => document.querySelectorAll('img[src^=\"data:image\"]').length")
            final_imgs = frame.evaluate("() => document.querySelectorAll('img').length")
            final_h2 = frame.evaluate("() => document.querySelectorAll('h2').length")
            body_len = frame.evaluate("() => document.body.innerText.length")
            has_adsense = frame.evaluate("() => document.querySelectorAll('ins.adsbygoogle').length")

            result = {
                "success": True,
                "steps": steps,
                "summary": {
                    "base64_remaining": final_b64,
                    "total_images": final_imgs,
                    "h2_count": final_h2,
                    "body_length": body_len,
                    "adsense_count": has_adsense,
                    "images_replaced": result_info.get("replaced", 0) if isinstance(result_info, dict) else 0,
                },
            }
            logger.info(f"edit_latest_draft 완료: {result['summary']}")
            return result

        except Exception as e:
            if page:
                _save_error_screenshot(page, prefix="edit_draft_error")
            steps.append({"step": "오류", "status": "failed", "error": str(e)})
            return {"success": False, "error": str(e), "steps": steps}

        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass


# ─── 내부 헬퍼 함수들 ───


def _insert_thumbnail_and_set_representative(page, thumbnail_path: str, blog_id: str):
    """Imagen 썸네일을 본문 맨 앞에 삽입하고 대표이미지로 설정합니다.

    1. 이미지 파일을 base64로 읽어 TinyMCE insertContent로 맨 앞에 삽입
    2. 티스토리 대표이미지 설정 UI 클릭
    """
    # 1) 이미지를 base64로 변환하여 TinyMCE 본문 맨 앞에 삽입
    with open(thumbnail_path, "rb") as f:
        img_bytes = f.read()
    img_b64 = base64.b64encode(img_bytes).decode("ascii")

    # TinyMCE에서 본문 맨 앞에 썸네일 이미지 삽입
    page.evaluate("""(b64) => {
        const editor = tinymce.activeEditor;
        if (!editor) return;
        const currentContent = editor.getContent();
        const thumbHtml = '<p style="text-align:center;">' +
            '<img src="data:image/webp;base64,' + b64 + '" ' +
            'style="max-width:100%;height:auto;border-radius:8px;" ' +
            'alt="썸네일" />' +
            '</p><p>&nbsp;</p>';
        editor.setContent(thumbHtml + currentContent);
    }""", img_b64)
    logger.info("썸네일 이미지 본문 맨 앞에 삽입 완료")
    time.sleep(1)

    # 2) 대표이미지 설정: 티스토리 에디터 우측 패널에서 대표이미지 버튼 클릭
    try:
        _set_tistory_representative_image(page)
    except Exception as e:
        logger.warning(f"대표이미지 설정 실패 (본문 첫 이미지로 자동 설정됨): {e}")


def _set_tistory_representative_image(page):
    """티스토리 에디터에서 대표이미지를 설정합니다.

    티스토리 에디터에서 본문 첫 번째 이미지를 자동으로 대표이미지로 설정.
    1. 우측 패널의 '대표이미지' 영역 클릭
    2. 첫 번째 이미지 선택
    3. 적용 버튼 클릭
    """
    # 방법 1: 대표이미지 설정 버튼 찾기 (여러 셀렉터 시도)
    selectors = [
        'button.btn_thumb',                    # 대표이미지 설정 버튼
        '.thumb_set button',                   # 썸네일 설정 영역
        'button[class*="thumb"]',              # thumb 관련 버튼
        '.representativeImage button',         # 대표이미지 영역
        '#sidebar button.btn_set',             # 사이드바 설정 버튼
    ]

    thumb_btn = None
    for sel in selectors:
        thumb_btn = page.query_selector(sel)
        if thumb_btn:
            logger.info(f"대표이미지 버튼 발견: {sel}")
            break

    if not thumb_btn:
        # 방법 2: 텍스트로 버튼 찾기
        thumb_btn = page.evaluate("""() => {
            const btns = document.querySelectorAll('button, a, div[role="button"]');
            for (const btn of btns) {
                const text = btn.textContent.trim();
                if (text.includes('대표') || text.includes('썸네일') || text.includes('thumbnail')) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }""")
        if thumb_btn:
            logger.info("대표이미지 버튼 텍스트로 클릭 완료")
            time.sleep(1)

            # 첫 번째 이미지 선택
            page.evaluate("""() => {
                // 모달 또는 패널에서 첫 이미지 클릭
                const imgs = document.querySelectorAll('.thumb_list img, .layer_thumb img, [class*="thumb"] img');
                if (imgs.length > 0) {
                    imgs[0].click();
                    return true;
                }
                return false;
            }""")
            time.sleep(0.5)

            # 적용/확인 버튼
            page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const text = btn.textContent.trim();
                    if (text === '적용' || text === '확인' || text === '선택') {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""")
            return

    if thumb_btn and not isinstance(thumb_btn, bool):
        thumb_btn.click()
        time.sleep(1)
        # 첫 번째 이미지 자동 선택 + 적용
        page.evaluate("""() => {
            const imgs = document.querySelectorAll('.thumb_list img, .layer_thumb img, [class*="thumb"] img');
            if (imgs.length > 0) imgs[0].click();
        }""")
        time.sleep(0.5)
        page.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const text = btn.textContent.trim();
                if (text === '적용' || text === '확인' || text === '선택') {
                    btn.click();
                    return;
                }
            }
        }""")
        logger.info("대표이미지 설정 완료")
    else:
        logger.info("대표이미지 버튼 없음 — 본문 첫 이미지가 자동 대표이미지로 사용됨")


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


def _type_body_html(page, body_html: str, image_urls: list = None):
    """TinyMCE iframe 안에서 타이핑 방식으로 본문을 입력합니다.
    ##H2:소제목##, ##H3:소제목##, ##AD## 마크업을 인식하여 처리합니다.
    images: 이미지 파일 경로 리스트. H2 소제목 뒤에 순서대로 삽입."""
    import re as _re

    if image_urls is None:
        image_urls = []

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
    # 애드센스 HTML 블록을 ##AD## 마커로 보존 (HTML 스트립 전에 처리)
    text = _re.sub(
        r'<div class="ad-container"[^>]*>.*?</div>',
        '##AD##',
        text,
        flags=_re.DOTALL,
    )
    # 인라인 마크다운 → HTML 변환 (HTML 스트립 전에, <strong>/<em> 보존)
    # HTML <strong>/<em> 태그를 마크다운 마커로 임시 변환
    text = _re.sub(r'<strong>(.*?)</strong>', r'**\1**', text, flags=_re.DOTALL)
    text = _re.sub(r'<b>(.*?)</b>', r'**\1**', text, flags=_re.DOTALL)
    text = _re.sub(r'<em>(.*?)</em>', r'*\1*', text, flags=_re.DOTALL)
    text = _re.sub(r'<i>(.*?)</i>', r'*\1*', text, flags=_re.DOTALL)
    text = _re.sub(r'<br\s*/?>', '\n', text)
    text = _re.sub(r'</(?:p|div|li|tr)>', '\n', text)
    text = _re.sub(r'<[^>]+>', '', text)
    # 마크다운 ## / ### 형식도 ##H2/H3## 마크업으로 변환
    text = _re.sub(r'^###\s+(.+)$', r'##H3:\1##', text, flags=_re.MULTILINE)
    text = _re.sub(r'^##\s+(.+)$', r'##H2:\1##', text, flags=_re.MULTILINE)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    # 마크다운 본문에 ##AD##가 없으면 H2 기준으로 자동 삽입 (2번째, 4번째 H2 뒤)
    if '##AD##' not in text:
        _lines = text.split('\n')
        _h2_indices = [j for j, ln in enumerate(_lines) if _re.match(r'##H2:.+?##', ln.strip())]
        for _target in reversed([1, 3]):  # 2번째(idx 1), 4번째(idx 3) H2 뒤
            if _target < len(_h2_indices):
                _insert_at = _h2_indices[_target] + 1
                _lines.insert(_insert_at, '##AD##')
        text = '\n'.join(_lines)

    # 줄 단위로 처리 (마크다운 표 블록은 먼저 추출)
    h2_count = 0  # H2 소제목 카운터 (이미지 삽입용)
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            frame.page.keyboard.press("Enter")
            time.sleep(0.05)
            i += 1
            continue

        # 마크다운 표 감지: | 로 시작하는 연속 줄 (최소 2줄)
        if stripped.startswith("|") and "|" in stripped[1:]:
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            if len(table_lines) >= 2:
                table_html = _markdown_table_to_html(table_lines)
                if table_html:
                    page.evaluate(
                        "(html) => { if(tinymce.activeEditor) tinymce.activeEditor.insertContent(html); }",
                        table_html,
                    )
                    time.sleep(0.5)
                    logger.info(f"마크다운 표 HTML 삽입 ({len(table_lines)}행)")
                    continue
            # 표가 아닌 경우 일반 텍스트로 처리 (아래로 fall-through)
            for tl in table_lines:
                frame.page.keyboard.type(tl.strip(), delay=random.randint(40, 120))
                frame.page.keyboard.press("Enter")
                time.sleep(0.1)
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

            # H2 뒤에 Unsplash 이미지 삽입
            if h2_count < len(image_urls):
                _insert_image_url_into_tinymce(page, image_urls[h2_count], alt_text=heading_text)
            h2_count += 1
            i += 1
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
            i += 1
            continue

        # ##AD## 처리: iframe DOM에 직접 script+ins 삽입 (TinyMCE 우회)
        if stripped == '##AD##':
            try:
                frame.evaluate("""() => {
                    const body = document.body;
                    if (!body) return;
                    // 현재 커서 위치 또는 마지막 요소 뒤에 삽입
                    const sel = window.getSelection();
                    let refNode = body.lastChild;
                    if (sel && sel.rangeCount > 0) {
                        refNode = sel.getRangeAt(0).endContainer;
                        if (refNode.nodeType === 3) refNode = refNode.parentNode;
                    }
                    // ins 태그 생성
                    const ins = document.createElement('ins');
                    ins.className = 'adsbygoogle';
                    ins.style.cssText = 'display:block;text-align:center;';
                    ins.setAttribute('data-ad-layout', 'in-article');
                    ins.setAttribute('data-ad-format', 'fluid');
                    ins.setAttribute('data-ad-client', 'ca-pub-1646757278810260');
                    ins.setAttribute('data-ad-slot', '3141593954');
                    // script 태그 생성
                    const script = document.createElement('script');
                    script.async = true;
                    script.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1646757278810260';
                    script.setAttribute('crossorigin', 'anonymous');
                    // push script
                    const pushScript = document.createElement('script');
                    pushScript.textContent = '(adsbygoogle = window.adsbygoogle || []).push({});';
                    // DOM에 삽입
                    if (refNode && refNode !== body) {
                        refNode.after(pushScript);
                        refNode.after(ins);
                        refNode.after(script);
                    } else {
                        body.appendChild(script);
                        body.appendChild(ins);
                        body.appendChild(pushScript);
                    }
                }""")
                time.sleep(0.5)
                logger.info("애드센스 DOM 직접 삽입 완료 (script+ins)")
            except Exception as e:
                logger.warning(f"애드센스 삽입 실패: {e}")
            i += 1
            continue

        # 일반 텍스트 — 인라인 마크다운이 있으면 insertContent, 없으면 keyboard.type
        if '**' in stripped or ('*' in stripped and _re.search(r'(?<!\*)\*(?!\*)', stripped)):
            # 마크다운 인라인 → HTML 변환
            html_line = stripped
            html_line = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html_line)
            html_line = _re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', html_line)
            html_line = f'<p>{html_line}</p>'
            page.evaluate(
                "(html) => { if(tinymce.activeEditor) tinymce.activeEditor.insertContent(html); }",
                html_line,
            )
            time.sleep(0.2)
        else:
            frame.page.keyboard.type(stripped, delay=random.randint(40, 120))
            frame.page.keyboard.press("Enter")
            time.sleep(random.uniform(0.1, 0.3))
        i += 1

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

    # JavaScript로 임시저장/완료 버튼 찾기
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
            // 완료 버튼 (이미 발행된 글 수정 시)
            for (const btn of buttons) {
                const text = btn.textContent.trim();
                if (text === '완료') {
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


# ─── 이미지 생성 함수 (naver_playwright.py 에서 이식) ───


def _translate_to_english(text: str) -> str:
    """한국어 텍스트를 영어 스톡사진 검색 키워드로 변환합니다."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    f"You are a stock photo search keyword generator.\n"
                    f"Given a Korean heading from a blog post, extract the CORE CONCEPT "
                    f"and convert it to 1-3 simple English keywords for stock photo search.\n\n"
                    f"Rules:\n"
                    f"- IGNORE proper nouns, brand names, and software names "
                    f"(e.g. 파워토이, 팬시존, 갤럭시, 카카오톡, 엑셀, 크롬)\n"
                    f"- Focus on the FUNCTIONAL meaning or activity described\n"
                    f"- Use generic, visual terms that stock photo sites understand\n"
                    f"- Examples:\n"
                    f"  '파워토이 팬시존 설치 방법' → 'computer screen layout'\n"
                    f"  '카카오톡 채팅 백업 방법' → 'smartphone messaging'\n"
                    f"  '엑셀 함수 정리' → 'spreadsheet data'\n"
                    f"  '갤럭시 배터리 절약 팁' → 'smartphone battery'\n"
                    f"  '화면 분할 프로그램 추천' → 'dual monitor workspace'\n"
                    f"  '벚꽃 명소 추천' → 'cherry blossom'\n"
                    f"- Output ONLY the keywords, nothing else.\n\n"
                    f"Text: {text}"
                ),
            }],
        )
        result = resp.content[0].text.strip()
        logger.info(f"이미지 키워드 번역: '{text}' → '{result}'")
        return result
    except Exception as e:
        logger.warning(f"번역 실패, 원문 사용: {e}")
        return text


def _generate_imagen_thumbnail(title: str, category: str = "it") -> str:
    """Imagen 4로 썸네일 이미지(800x800) 생성 → temp 파일 경로 반환."""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY가 설정되지 않았습니다.")

    # 제목 → 영문 키워드
    en_query = _translate_to_english(title)

    # 카테고리별 프롬프트
    if category in ("it",):
        prompt = f"clean minimal tech illustration, {en_query}, flat design, soft gradient background, no text, no letters, no words"
    elif category in ("travel", "여행"):
        prompt = f"beautiful travel photo style, {en_query}, natural light, vibrant colors, no text, no letters, no words"
    elif category in ("food", "음식"):
        prompt = f"delicious food photography, {en_query}, warm lighting, close-up, no text, no letters, no words"
    else:
        prompt = f"clean professional blog thumbnail, {en_query}, bright colors, modern design, no text, no letters, no words"

    logger.info(f"[Imagen 썸네일] 프롬프트: {prompt}")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/imagen-4.0-generate-001:predict?key={api_key}"
    )
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": "1:1"},
    }

    resp = http_requests.post(url, json=payload, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Imagen 4 API 오류 {resp.status_code}: {resp.text[:200]}")

    predictions = resp.json().get("predictions", [])
    if not predictions or not predictions[0].get("bytesBase64Encoded"):
        raise RuntimeError("Imagen 4 응답에 이미지가 없습니다.")

    img_bytes = base64.b64decode(predictions[0]["bytesBase64Encoded"])
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(img_bytes))
    img = img.resize((800, 800), Image.LANCZOS)

    tmp = tempfile.NamedTemporaryFile(suffix=".webp", delete=False)
    img.convert("RGB").save(tmp, format="WEBP", quality=85)
    tmp.close()
    logger.info(f"[Imagen 썸네일] 생성 완료: {tmp.name}")
    return tmp.name


def _fetch_pexels_image(query: str, width: int = 800, height: int = 533) -> str:
    """Pexels API로 이미지를 검색하여 첫 번째 결과를 다운로드합니다."""
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY가 설정되지 않았습니다.")

    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": 1, "orientation": "landscape"}
    resp = http_requests.get(
        "https://api.pexels.com/v1/search",
        headers=headers, params=params, timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Pexels API 오류 {resp.status_code}")

    photos = resp.json().get("photos", [])
    if not photos:
        raise RuntimeError("Pexels 검색 결과가 없습니다.")

    img_url = photos[0]["src"]["large"]
    img_resp = http_requests.get(img_url, timeout=30)
    if img_resp.status_code != 200:
        raise RuntimeError(f"Pexels 이미지 다운로드 실패 {img_resp.status_code}")

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(img_resp.content)
    tmp.close()

    _resize_image(tmp.name, width, height)
    logger.info(f"Pexels fallback 이미지: {query} → {tmp.name}")
    return tmp.name


def _resize_image(path: str, width: int, height: int) -> str:
    """이미지를 정확한 크기로 리사이즈하여 같은 경로에 저장합니다."""
    from PIL import Image
    with Image.open(path) as img:
        img = img.convert("RGB")
        img = img.resize((width, height), Image.LANCZOS)
        img.save(path, "PNG", optimize=True)
    return path


def _generate_imagen(prompt: str) -> str:
    """Imagen 4 Fast로 본문 이미지(800x533)를 생성합니다."""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY가 설정되지 않았습니다.")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/imagen-4.0-fast-generate-001:predict?key={api_key}"
    )
    prefix = (
        "Photorealistic nature photo only. Absolutely no text, no words, "
        "no letters, no captions, no labels, no watermarks, no titles "
        "anywhere in the image. "
    )
    payload = {
        "instances": [{"prompt": prefix + prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": "4:3"},
    }

    resp = http_requests.post(url, json=payload, timeout=60)
    if resp.status_code == 429:
        logger.warning("Imagen API 429 → Pexels fallback")
        return _fetch_pexels_image(prompt, 800, 533)
    if resp.status_code != 200:
        raise RuntimeError(f"Imagen API 오류 {resp.status_code}: {resp.text[:200]}")

    predictions = resp.json().get("predictions", [])
    if not predictions or not predictions[0].get("bytesBase64Encoded"):
        raise RuntimeError("Imagen 응답에 이미지가 없습니다.")

    img_bytes = base64.b64decode(predictions[0]["bytesBase64Encoded"])
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(img_bytes)
    tmp.close()

    _resize_image(tmp.name, 800, 533)
    return tmp.name


def _markdown_table_to_html(table_lines: list[str]) -> str:
    """마크다운 표(|---|---| 형식)를 HTML <table>로 변환합니다."""
    rows = []
    is_header = True
    for line in table_lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        # 구분선(|---|---| ) 무시
        if _re_module.match(r'^\|[\s\-:|]+\|$', line):
            is_header = False
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if is_header:
            row_html = "".join(f"<th>{c}</th>" for c in cells)
            rows.append(f"<tr>{row_html}</tr>")
        else:
            row_html = "".join(f"<td>{c}</td>" for c in cells)
            rows.append(f"<tr>{row_html}</tr>")

    if not rows:
        return ""
    # 첫 번째 행을 thead로
    thead = f"<thead>{rows[0]}</thead>" if rows else ""
    tbody = "<tbody>" + "".join(rows[1:]) + "</tbody>" if len(rows) > 1 else ""
    return (
        '<table style="border-collapse:collapse;width:100%;margin:1em 0;" '
        'border="1" cellpadding="8" cellspacing="0">'
        f'{thead}{tbody}</table><p>&nbsp;</p>'
    )


def _fetch_unsplash_image_url(query: str) -> str:
    """Unsplash API로 이미지를 검색하여 첫 번째 결과 URL을 반환합니다."""
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    if not access_key or access_key == "여기에Access키붙여넣기":
        raise RuntimeError("UNSPLASH_ACCESS_KEY가 설정되지 않았습니다.")

    resp = http_requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "orientation": "landscape", "per_page": 5},
        headers={"Authorization": f"Client-ID {access_key}"},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Unsplash API 오류 {resp.status_code}")

    results = resp.json().get("results", [])
    if not results:
        # fallback: 첫 단어만으로 재검색
        first_word = query.split()[0] if query.split() else query
        if first_word != query:
            resp2 = http_requests.get(
                "https://api.unsplash.com/search/photos",
                params={"query": first_word, "orientation": "landscape", "per_page": 5},
                headers={"Authorization": f"Client-ID {access_key}"},
                timeout=10,
            )
            if resp2.status_code == 200:
                results = resp2.json().get("results", [])
        if not results:
            raise RuntimeError(f"Unsplash 검색 결과 없음: {query}")

    photo = results[0]
    url = photo["urls"]["raw"] + "?w=800&h=533&fit=crop&fm=webp&q=80"
    logger.info(f"Unsplash 이미지: {query} → {url[:80]}...")
    return url


def _upload_image_to_tistory(page, img_path: str, blog_id: str) -> str:
    """티스토리 이미지 업로드 API로 이미지를 업로드하고 CDN URL을 반환합니다."""
    with open(img_path, "rb") as f:
        img_bytes = f.read()
    img_b64 = base64.b64encode(img_bytes).decode("ascii")
    fname = os.path.basename(img_path)

    result = page.evaluate("""([b64, fname, blogId]) => {
        return new Promise((resolve, reject) => {
            const byteStr = atob(b64);
            const bytes = new Uint8Array(byteStr.length);
            for (let i = 0; i < byteStr.length; i++) bytes[i] = byteStr.charCodeAt(i);
            const blob = new Blob([bytes], {type: 'image/png'});
            const fd = new FormData();
            fd.append('image', blob, fname);
            fd.append('editor', 'tinymce');
            const url = `https://${blogId}.tistory.com/manage/post/image-upload`;
            fetch(url, {method: 'POST', body: fd, credentials: 'include'})
                .then(r => r.json())
                .then(data => resolve(JSON.stringify(data)))
                .catch(e => reject(e.message));
        });
    }""", [img_b64, fname, blog_id])

    data = json.loads(result)
    # 티스토리 응답에서 URL 추출
    img_url = data.get("url") or data.get("imageUrl") or data.get("src", "")
    if not img_url:
        raise RuntimeError(f"이미지 URL을 받지 못했습니다: {data}")
    logger.info(f"티스토리 이미지 업로드 완료: {img_url}")
    return img_url


def _insert_image_url_into_tinymce(page, img_url: str, alt_text: str = ""):
    """TinyMCE iframe에 이미지 URL을 삽입합니다."""
    try:
        img_html = (
            f'<figure style="margin:1.2em 0;text-align:center;">'
            f'<img src="{img_url}" '
            f'alt="{alt_text}" '
            f'style="max-width:100%;height:auto;border-radius:8px;" />'
            f'</figure><p>&nbsp;</p>'
        )
        page.evaluate(
            "(html) => { if(tinymce.activeEditor) tinymce.activeEditor.insertContent(html); }",
            img_html,
        )
        time.sleep(1)
        logger.info(f"TinyMCE 이미지 삽입 완료: {alt_text}")
        return True
    except Exception as e:
        logger.warning(f"TinyMCE 이미지 삽입 실패: {e}")
        return False


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
