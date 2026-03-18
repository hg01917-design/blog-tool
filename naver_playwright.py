"""
네이버 블로그 Playwright 자동 발행 모듈.
- 쿠키 저장/로드 기반 로그인
- Smart Editor ONE 자동 입력 (제목, 본문 HTML, 태그)
- 자동 발행
"""

import json
import os
import re
import time
import random
import base64
import logging
import tempfile

import requests as http_requests
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COOKIE_PATH = os.path.join(_APP_DIR, "data", "naver_cookies.json")

# 모듈 레벨 rate limit
_last_publish_time = 0
PUBLISH_INTERVAL = 30  # 초

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


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


def _get_cookie_path() -> str:
    return os.environ.get("NAVER_COOKIE_PATH", DEFAULT_COOKIE_PATH)


def _get_profile_dir(account_id: str = "daonna525") -> str:
    """Persistent Context용 네이버 브라우저 프로파일 디렉토리."""
    return os.path.join(_APP_DIR, "browser_profiles", f"{account_id}_naver")


def _get_blog_id() -> str:
    return os.environ.get("NAVER_BLOG_ID", "")


def _open_persistent_context(p, account_id: str = "daonna525", headless: bool = True):
    """Persistent browser context를 열어 반환합니다.
    LOCAL_MODE면 로컬 크롬 프로필을 사용합니다."""
    if _is_local_mode():
        chrome_profile = _get_local_chrome_profile()
        context = p.chromium.launch_persistent_context(
            user_data_dir=chrome_profile,
            headless=False,
            channel="chrome",
            viewport={"width": 1280, "height": 900},
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        return context
    profile_dir = _get_profile_dir(account_id)
    os.makedirs(profile_dir, exist_ok=True)
    context = p.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=headless,
        viewport={"width": 1280, "height": 900},
        user_agent=_BROWSER_UA,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    return context


def cookies_exist(account_id: str = "daonna525") -> bool:
    """프로파일 디렉토리가 존재하는지 확인. LOCAL_MODE면 로컬 크롬 확인."""
    if _is_local_mode():
        try:
            _get_local_chrome_profile()
            return True
        except RuntimeError:
            return False
    profile_dir = _get_profile_dir(account_id)
    if os.path.isdir(profile_dir):
        return True
    path = _get_cookie_path()
    return os.path.exists(path) and os.path.getsize(path) > 10


def login_and_save_cookies(timeout_sec: int = 120, account_id: str = "daonna525") -> dict:
    """Persistent Context로 브라우저를 열어 수동 로그인합니다."""
    with sync_playwright() as p:
        context = _open_persistent_context(p, account_id, headless=False)

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded")
            logger.info("네이버 로그인 페이지 열림. 수동 로그인을 진행하세요.")

            deadline = time.time() + timeout_sec
            logged_in = False
            while time.time() < deadline:
                if "nid.naver.com" not in page.url and "nidlogin" not in page.url:
                    logged_in = True
                    break
                time.sleep(1)

            if not logged_in:
                return {"success": False, "error": f"{timeout_sec}초 내에 로그인이 완료되지 않았습니다."}

            blog_id = _get_blog_id()
            if blog_id:
                page.goto(f"https://blog.naver.com/{blog_id}", wait_until="domcontentloaded")
                time.sleep(2)

            logger.info(f"Persistent Context 로그인 완료: {_get_profile_dir(account_id)}")
            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            context.close()


def upload_cookies(cookies_json: str, account_id: str = "daonna525") -> dict:
    """JSON 쿠키를 Persistent Context 프로파일에 주입합니다."""
    try:
        cookies = json.loads(cookies_json)
        if not isinstance(cookies, list):
            return {"success": False, "error": "쿠키는 JSON 배열이어야 합니다."}

        with sync_playwright() as p:
            context = _open_persistent_context(p, account_id, headless=True)
            try:
                context.add_cookies(cookies)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://www.naver.com", wait_until="domcontentloaded")
                time.sleep(2)
            finally:
                context.close()

        return {"success": True, "cookie_count": len(cookies)}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON 파싱 실패: {e}"}


def _save_step_screenshot(page, entry_id: str, filename: str):
    """발행 단계별 스크린샷을 저장합니다."""
    if not entry_id or page is None:
        return
    try:
        ss_dir = os.path.join(_APP_DIR, "screenshots", entry_id)
        os.makedirs(ss_dir, exist_ok=True)
        path = os.path.join(ss_dir, filename)
        page.screenshot(path=path)
        logger.info(f"단계 스크린샷 저장: {path}")
    except Exception as e:
        logger.warning(f"스크린샷 저장 실패 ({filename}): {e}")


def publish_to_naver(title: str, body_html: str, tags: list[str],
                     blog_id: str = None, entry_id: str = None) -> dict:
    """네이버 블로그에 글을 자동 발행합니다.

    발행 순서:
    1. 썸네일(Gemini) → 대표 이미지 지정
    2. 제목 입력
    3. 소제목별: 소제목 → 이미지(Imagen) → 본문 반복
    4. 태그 입력
    5. 임시저장

    Args:
        title: 글 제목
        body_html: HTML 본문 (h2/h3 소제목 포함)
        tags: 태그 리스트 (최대 10개)
        blog_id: 네이버 블로그 ID (None이면 환경변수에서 읽음)
    """
    global _last_publish_time

    if not blog_id:
        blog_id = _get_blog_id()
    if not blog_id:
        return {"success": False, "error": "NAVER_BLOG_ID가 설정되지 않았습니다.", "steps": []}

    # entry_id에서 account_id 추출 (예: daonna525_naver → daonna525)
    _account_id = entry_id.split("_")[0] if entry_id and "_" in entry_id else "daonna525"
    if not cookies_exist(_account_id):
        return {"success": False, "error": "브라우저 프로파일이 없습니다. 먼저 로그인해주세요.", "steps": []}

    # Rate limit
    now = time.time()
    elapsed = now - _last_publish_time
    if _last_publish_time > 0 and elapsed < PUBLISH_INTERVAL:
        wait = int(PUBLISH_INTERVAL - elapsed)
        return {"success": False, "error": f"발행 간격 제한: {wait}초 후 다시 시도해주세요.", "steps": []}

    steps = []
    tmp_files = []  # 임시 파일 추적 (cleanup용)

    # 본문을 섹션으로 파싱
    sections = _parse_sections(body_html)

    with sync_playwright() as p:
        context = _open_persistent_context(p, _account_id)

        page = None
        try:
            steps.append({"step": "프로파일 로드", "status": "success"})

            page = context.pages[0] if context.pages else context.new_page()

            # 2) 글쓰기 페이지 이동
            write_url = f"https://blog.naver.com/{blog_id}/postwrite"
            page.goto(write_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            if "nidlogin" in page.url or "nid.naver.com" in page.url:
                steps.append({"step": "로그인 확인", "status": "failed"})
                return {"success": False, "error": "쿠키가 만료되었습니다.", "steps": steps}
            steps.append({"step": "로그인 확인", "status": "success"})
            _save_step_screenshot(page, entry_id, "01_login.png")

            # 3) 에디터 로드 + 팝업 닫기
            page.wait_for_selector(".se-content", timeout=30000)
            steps.append({"step": "에디터 로드", "status": "success"})

            time.sleep(1)
            alert_popup = page.query_selector('.se-popup-alert-confirm')
            if alert_popup:
                dismiss_btn = alert_popup.query_selector('button.se-popup-button-cancel, button:last-child')
                if dismiss_btn:
                    dismiss_btn.click()
                    time.sleep(1)

            help_close = page.query_selector('button.se-help-panel-close-button')
            if help_close:
                help_close.click()
                time.sleep(1)

            # ── 4) 제목 입력 ──
            title_sel = ".se-documentTitle .se-text-paragraph"
            page.wait_for_selector(title_sel, timeout=10000)
            page.click(title_sel)
            time.sleep(0.5)
            page.keyboard.type(title, delay=random.randint(40, 120))
            steps.append({"step": "제목 입력", "status": "success"})
            _save_step_screenshot(page, entry_id, "02_title.png")
            time.sleep(random.uniform(1.0, 3.0))

            # ── 5) 본문 영역으로 이동 ──
            page.keyboard.press("Tab")
            time.sleep(random.uniform(1.0, 3.0))

            # ── 6) 대표 이미지 생성 + 업로드 + 대표 지정 ──
            try:
                thumb_path = _generate_thumbnail_with_text(title)
                tmp_files.append(thumb_path)

                # 사진 버튼 → file input으로 업로드
                _dismiss_overlays(page)
                photo_btn = page.query_selector('.se-image-toolbar-button')
                if photo_btn:
                    photo_btn.click()
                    time.sleep(1)
                    file_input = page.query_selector('#hidden-file')
                    if file_input:
                        file_input.set_input_files(thumb_path)
                        time.sleep(3)

                        # 업로드된 이미지 클릭 → 대표 버튼 확인
                        img_el = page.query_selector('.se-component.se-image img.se-image-resource')
                        if not img_el:
                            img_el = page.query_selector('.se-component.se-image img')
                        if img_el:
                            img_el.click()
                            time.sleep(0.5)
                            # 대표 버튼이 미선택 상태면 클릭
                            rep_btn = page.query_selector('button.se-set-rep-image-button:not(.se-is-selected)')
                            if rep_btn:
                                rep_btn.click()
                                time.sleep(0.5)

                        steps.append({"step": "대표이미지", "status": "success"})
                    else:
                        steps.append({"step": "대표이미지", "status": "skipped", "error": "file input 없음"})
                else:
                    steps.append({"step": "대표이미지", "status": "skipped", "error": "사진 버튼 없음"})
            except Exception as e:
                logger.warning(f"대표이미지 생성 실패: {e}")
                steps.append({"step": "대표이미지", "status": "failed", "error": str(e)})
            _save_step_screenshot(page, entry_id, "04_image.png")

            # ── 7) 섹션별 반복: 소제목 → 이미지 → 본문 ──
            for idx, section in enumerate(sections):
                heading = section["heading"]
                body_text = _html_to_plain(section["body"])

                # 7-1) 소제목 입력 (있으면 서식 변경)
                if heading:
                    # 소제목 서식 적용
                    _dismiss_overlays(page)
                    fmt_btn = page.query_selector('.se-text-format-toolbar-button')
                    if fmt_btn:
                        fmt_btn.click()
                        time.sleep(0.5)
                        section_title_btn = page.query_selector(
                            'button.se-toolbar-option-text-format-sectionTitle-button'
                        )
                        if section_title_btn:
                            section_title_btn.click()
                            time.sleep(0.3)

                    page.keyboard.type(heading, delay=random.randint(40, 120))
                    page.keyboard.press("Enter")
                    time.sleep(random.uniform(1.0, 3.0))

                    # 서식을 본문으로 복귀
                    fmt_btn = page.query_selector('.se-text-format-toolbar-button')
                    if fmt_btn:
                        fmt_btn.click()
                        time.sleep(0.5)
                        text_btn = page.query_selector(
                            'button.se-toolbar-option-text-format-text-button'
                        )
                        if text_btn:
                            text_btn.click()
                            time.sleep(0.3)

                    steps.append({"step": f"소제목{idx+1}", "status": "success", "text": heading})

                # 7-2) 소제목별 이미지 생성 + 업로드 (소제목이 있는 섹션만)
                if heading:
                    try:
                        en_prompt = _translate_to_english(heading)
                        img_path = _generate_imagen(en_prompt)
                        tmp_files.append(img_path)

                        # 사진 버튼 → file input
                        _dismiss_overlays(page)
                        photo_btn = page.query_selector('.se-image-toolbar-button')
                        if photo_btn:
                            photo_btn.click()
                            time.sleep(1)
                            file_input = page.query_selector('#hidden-file')
                            if file_input:
                                file_input.set_input_files(img_path)
                                time.sleep(3)
                                steps.append({"step": f"이미지{idx+1}", "status": "success"})
                            else:
                                steps.append({"step": f"이미지{idx+1}", "status": "skipped"})
                    except Exception as e:
                        logger.warning(f"이미지{idx+1} 생성 실패: {e}")
                        steps.append({"step": f"이미지{idx+1}", "status": "failed", "error": str(e)})

                # 7-3) 본문 텍스트 입력 (문단 단위 타이핑)
                if body_text:
                    # 이미지 업로드 후 마지막 텍스트 영역 클릭
                    body_ps = page.query_selector_all(".se-component.se-text .se-text-paragraph")
                    if body_ps:
                        body_ps[-1].click()
                    time.sleep(0.3)

                    # 빈 줄 기준으로 문단 분리
                    paragraphs = re.split(r'\n\s*\n', body_text)
                    for pi, para in enumerate(paragraphs):
                        lines = [l for l in para.split('\n') if l.strip()]
                        for li, line in enumerate(lines):
                            page.keyboard.type(line.strip(), delay=random.randint(40, 120))
                            if li < len(lines) - 1:
                                page.keyboard.press("Enter")
                                time.sleep(0.05)
                        # 문단 사이 Enter 두 번 (마지막 문단 제외)
                        if pi < len(paragraphs) - 1:
                            page.keyboard.press("Enter")
                            time.sleep(0.05)
                            page.keyboard.press("Enter")
                            time.sleep(0.05)
                    page.keyboard.press("Enter")
                    time.sleep(random.uniform(1.0, 3.0))

            steps.append({"step": "본문 입력", "status": "success", "sections": len(sections)})
            _save_step_screenshot(page, entry_id, "03_body.png")

            time.sleep(random.uniform(1.0, 3.0))

            # ── 8) 발행 설정 열기 → 태그 입력 ──
            _dismiss_overlays(page)
            publish_btn = page.query_selector('button[class*="publish_btn"]')
            if not publish_btn:
                _save_error_screenshot(page)
                return {"success": False, "error": "발행 버튼을 찾지 못했습니다.", "steps": steps}

            publish_btn.click()
            time.sleep(2)
            page.wait_for_selector('[class*="layer_popup"][class*="is_show"]', timeout=10000)

            tag_list = tags[:10]
            if tag_list:
                try:
                    tag_input = page.query_selector("#tag-input")
                    if tag_input:
                        tag_input.click()
                        time.sleep(0.3)
                        for tag in tag_list:
                            tag_input.fill("")
                            tag_input.type(tag.strip(), delay=random.randint(40, 120))
                            page.keyboard.press("Enter")
                            time.sleep(random.uniform(1.0, 3.0))
                        steps.append({"step": "태그 입력", "status": "success", "count": len(tag_list)})
                except Exception as e:
                    steps.append({"step": "태그 입력", "status": "failed", "error": str(e)})

            # ── 9) 임시저장 ──
            fold_btn = page.query_selector('button[class*="publish_fold_btn"]')
            if fold_btn:
                fold_btn.click()
                time.sleep(1)

            save_btn = page.query_selector('button[class*="save_btn"]')
            if not save_btn:
                _save_error_screenshot(page)
                return {"success": False, "error": "저장 버튼을 찾지 못했습니다.", "steps": steps}

            save_btn.click()
            time.sleep(3)

            post_url = page.url
            _last_publish_time = time.time()
            steps.append({"step": "임시저장", "status": "success", "url": post_url})
            _save_step_screenshot(page, entry_id, "05_done.png")

            return {"success": True, "post_url": post_url, "steps": steps}

        except PlaywrightTimeout as e:
            if page:
                _save_error_screenshot(page)
            steps.append({"step": "타임아웃", "status": "failed", "error": str(e)})
            return {"success": False, "error": f"타임아웃: {e}", "steps": steps}

        except Exception as e:
            if page:
                _save_error_screenshot(page)
            steps.append({"step": "오류", "status": "failed", "error": str(e)})
            return {"success": False, "error": str(e), "steps": steps}

        finally:
            # 임시 파일 정리
            for f in tmp_files:
                try:
                    if os.path.exists(f):
                        os.unlink(f)
                except Exception:
                    pass
            context.close()


def _translate_to_english(text: str) -> str:
    """한국어 텍스트를 영어 이미지 프롬프트로 변환합니다."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    f"Translate the following Korean text into a short English image prompt "
                    f"suitable for AI image generation. Output ONLY the English prompt, nothing else.\n\n"
                    f"Text: {text}"
                ),
            }],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.warning(f"번역 실패, 원문 사용: {e}")
        return text


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
    with Image.open(path) as img:
        img = img.convert("RGB")
        img = img.resize((width, height), Image.LANCZOS)
        img.save(path, "PNG", optimize=True)
    logger.info(f"이미지 리사이즈: {width}x{height} → {path}")
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

    # 800x533 리사이즈
    _resize_image(tmp.name, 800, 533)
    return tmp.name


def _generate_thumbnail_with_text(title: str) -> str:
    """Imagen 4 Fast로 배경 이미지를 생성 후 PIL로 제목 텍스트를 합성합니다.

    - 800x800 (1:1) 정사각형
    - 반투명 어두운 오버레이
    - 제목 한글 텍스트 중앙 배치 (나눔고딕)
    """
    # 1) Imagen으로 배경 이미지 생성 (1:1)
    en_prompt = _translate_to_english(title)
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY가 설정되지 않았습니다.")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/imagen-4.0-fast-generate-001:predict?key={api_key}"
    )
    thumb_prefix = (
        "Photorealistic photo only. Absolutely no text, no words, "
        "no letters, no typography, no captions, no labels, no watermarks, "
        "no titles anywhere in the image. "
    )
    payload = {
        "instances": [{"prompt": thumb_prefix + en_prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": "1:1"},
    }
    resp = http_requests.post(url, json=payload, timeout=60)
    if resp.status_code == 429:
        logger.warning("Imagen 썸네일 429 → Pexels fallback")
        try:
            pexels_path = _fetch_pexels_image(en_prompt, 800, 800)
        except Exception as pe:
            logger.warning(f"Pexels fallback도 실패: {pe}")
            raise RuntimeError(f"Imagen 429 + Pexels 실패: {pe}")
        tmp = type('', (), {'name': pexels_path})()  # tmp.name 호환
    elif resp.status_code != 200:
        raise RuntimeError(f"Imagen 썸네일 API 오류 {resp.status_code}: {resp.text[:200]}")
    else:
        predictions = resp.json().get("predictions", [])
        if not predictions or not predictions[0].get("bytesBase64Encoded"):
            raise RuntimeError("Imagen 썸네일 응답에 이미지가 없습니다.")

        img_bytes = base64.b64decode(predictions[0]["bytesBase64Encoded"])
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(img_bytes)
        tmp.close()

    # 2) PIL로 800x800 리사이즈 + 텍스트 합성
    img = Image.open(tmp.name).convert("RGB")
    img = img.resize((800, 800), Image.LANCZOS)

    # 반투명 어두운 오버레이
    overlay = Image.new("RGBA", (800, 800), (0, 0, 0, 120))
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)

    # 폰트 로드
    font_path = os.path.join(_APP_DIR, "fonts", "NanumGothicBold.ttf")
    font_size = 48
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        logger.warning(f"폰트 로드 실패: {font_path}, 기본 폰트 사용")
        font = ImageFont.load_default()

    # 제목 줄바꿈 처리 (한 줄 최대 약 14자)
    max_chars = 14
    lines = []
    for i in range(0, len(title), max_chars):
        lines.append(title[i:i + max_chars])

    # 전체 텍스트 높이 계산
    line_height = font_size + 12
    total_height = line_height * len(lines)
    y_start = (800 - total_height) // 2

    # 텍스트 그리기 (흰색, 중앙 정렬)
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (800 - text_width) // 2
        y = y_start + i * line_height

        # 텍스트 그림자 (가독성)
        draw.text((x + 2, y + 2), line, fill=(0, 0, 0, 200), font=font)
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)

    img = img.convert("RGB")
    img.save(tmp.name, "PNG", optimize=True)
    logger.info(f"썸네일 생성 완료: {tmp.name} (800x800, 텍스트 합성)")
    return tmp.name


def _parse_sections(body_html: str) -> list:
    """본문을 소제목 기준으로 섹션 분할합니다.

    ##H2:소제목## 마크업과 HTML <h2>/<h3> 태그 모두 지원.
    ##AD##, ##IMG:설명## 태그는 제거합니다.

    Returns: [{"heading": "소제목", "body": "본문텍스트"}, ...]
    """
    # ##AD## 태그 제거 (빈 줄로 대체)
    text = re.sub(r'##AD##', '', body_html)
    # ##IMG:설명## 태그 제거
    text = re.sub(r'##IMG:[^#]*##', '', text)

    # ##H2:소제목## 형식이 있으면 마크업 기반 파싱
    if '##H2:' in text or '##' in text:
        # ##H2:소제목## 또는 ##소제목## 패턴으로 분할
        parts = re.split(r'(?:##H2:|##)([^#]+)##', text)

        sections = []
        # parts[0]은 첫 소제목 앞 텍스트 (도입부)
        intro = parts[0].strip()
        if intro:
            sections.append({"heading": "", "body": intro})

        # 이후는 (소제목, 본문) 쌍
        for i in range(1, len(parts), 2):
            heading = parts[i].strip() if i < len(parts) else ""
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            if heading or body:
                sections.append({"heading": heading, "body": body})

        return sections if sections else [{"heading": "", "body": text.strip()}]

    # HTML h2/h3 태그 기반 파싱 (기존 방식)
    parts = re.split(r'(<h[23][^>]*>.*?</h[23]>)', text, flags=re.DOTALL)

    sections = []
    current_heading = ""
    current_body = ""

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if re.match(r'<h[23]', part):
            if current_heading or current_body:
                sections.append({"heading": current_heading, "body": current_body.strip()})
            current_heading = re.sub(r'<[^>]+>', '', part).strip()
            current_body = ""
        else:
            current_body += part

    if current_heading or current_body:
        sections.append({"heading": current_heading, "body": current_body.strip()})

    return sections


def _html_to_plain(html: str) -> str:
    """HTML/마크업을 줄바꿈 포함 평문으로 변환."""
    text = html
    # ##AD##, ##IMG## 태그 제거
    text = re.sub(r'##AD##', '', text)
    text = re.sub(r'##IMG:[^#]*##', '', text)
    # HTML 태그 처리
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</(?:p|div|h[1-6]|li|tr)>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    # 연속 빈 줄 정리
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _dismiss_overlays(page):
    """도움말, 팝업 등 오버레이 요소를 닫습니다."""
    try:
        # 도움말 오버레이 닫기
        page.evaluate("""() => {
            // 도움말 컨테이너 제거
            const help = document.querySelector('[class*="container__"]');
            if (help && help.querySelector('.se-help-title')) {
                help.remove();
            }
            // se-popup-dim 오버레이 제거
            document.querySelectorAll('.se-popup-dim').forEach(el => el.remove());
            // 기타 팝업 닫기 버튼 클릭
            const closeBtn = document.querySelector('[class*="close_btn"], .se-popup-close-button');
            if (closeBtn) closeBtn.click();
        }""")
        time.sleep(0.5)
    except Exception:
        pass


def _save_error_screenshot(page, prefix="naver_error"):
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
