#!/usr/bin/env python3
"""경주 벚꽃 대릉원 - 임시저장 + 검증 통합 테스트"""
import os, sys, json, time, random, logging, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("test_draft_v2")

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import anthropic

_DIR = os.path.dirname(os.path.abspath(__file__))
cookie_path = os.path.join(_DIR, "data", "tistory_cookies_baremi542.json")

# 1) 글 생성
client = anthropic.Anthropic()
keyword = "경주 벚꽃 대릉원"

title_resp = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=200,
    messages=[{"role": "user", "content": f"다음 키워드로 여행 블로그 글 제목 1개만 작성해줘. 다른 말 없이 제목만.\n키워드: {keyword}"}],
)
title = title_resp.content[0].text.strip().strip('"')
print(f"[제목] {title}")

body_resp = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=4000,
    system="여행 블로그 글을 작성합니다. ##H2:소제목## 형식으로 소제목을 넣어주세요. HTML 태그 사용 금지. 3개 소제목, 각 소제목 아래 3-5문장. ##AD## 마커를 소제목 2개 사이에 1회 삽입.",
    messages=[{"role": "user", "content": f"키워드: {keyword}\n제목: {title}\n\n본문만 작성해줘."}],
)
body = body_resp.content[0].text.strip()
print(f"[본문 길이] {len(body)}자")
print(f"[##AD## 포함] {'##AD##' in body}")

tags = ["경주", "벚꽃", "대릉원", "경주여행", "벚꽃명소"]

# 2) Playwright로 직접 임시저장 + 검증
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    )
    with open(cookie_path, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    context.add_cookies(cookies)
    page = context.new_page()

    # 글쓰기 페이지
    page.goto("https://nolja100.tistory.com/manage/newpost", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    # 로그인 확인
    if "accounts.kakao.com" in page.url or "auth/login" in page.url:
        page.screenshot(path=os.path.join(_DIR, "data", "test_v2_login_fail.png"))
        print("[실패] 쿠키 만료")
        browser.close()
        sys.exit(1)

    # 임시저장 복원 팝업 닫기
    try:
        page.evaluate("""() => {
            const buttons = document.querySelectorAll('button, a');
            for (const btn of buttons) {
                const text = btn.textContent.trim();
                if (text.includes('새 글 작성') || text.includes('새로 작성') || text.includes('취소') || text.includes('아니오')) {
                    btn.click(); return text;
                }
            }
            return null;
        }""")
        time.sleep(1)
    except Exception:
        pass

    # 에디터 대기
    page.wait_for_selector("#post-title-inp", timeout=15000)
    print("[OK] 에디터 로드")

    # 제목 입력
    title_el = page.query_selector("#post-title-inp")
    title_el.click()
    time.sleep(0.5)
    title_el.type(title, delay=60)
    print(f"[OK] 제목 입력: {title}")
    time.sleep(1)

    # TinyMCE 본문 입력
    page.wait_for_selector("#editor-tistory_ifr", timeout=15000)
    iframe_el = page.query_selector("#editor-tistory_ifr")
    frame = iframe_el.content_frame()
    body_el = frame.query_selector("body")
    body_el.click()
    time.sleep(0.5)

    # 줄 단위 타이핑
    text = body
    text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'##H2:\1##', text, flags=re.DOTALL)
    text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'##H3:\1##', text, flags=re.DOTALL)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</(?:p|div|li|tr)>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    ad_inserted = False
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped:
            frame.page.keyboard.press("Enter")
            continue

        h2_match = re.match(r'##H2:(.+?)##', stripped)
        if h2_match:
            heading = h2_match.group(1).strip()
            page.evaluate("() => { if(tinymce.activeEditor) tinymce.activeEditor.execCommand('FormatBlock', false, 'h2'); }")
            time.sleep(0.3)
            frame.page.keyboard.type(heading, delay=60)
            frame.page.keyboard.press("Enter")
            time.sleep(0.3)
            page.evaluate("() => { if(tinymce.activeEditor) tinymce.activeEditor.execCommand('FormatBlock', false, 'p'); }")
            continue

        h3_match = re.match(r'##H3:(.+?)##', stripped)
        if h3_match:
            heading = h3_match.group(1).strip()
            page.evaluate("() => { if(tinymce.activeEditor) tinymce.activeEditor.execCommand('FormatBlock', false, 'h3'); }")
            time.sleep(0.3)
            frame.page.keyboard.type(heading, delay=60)
            frame.page.keyboard.press("Enter")
            time.sleep(0.3)
            page.evaluate("() => { if(tinymce.activeEditor) tinymce.activeEditor.execCommand('FormatBlock', false, 'p'); }")
            continue

        if stripped == '##AD##':
            frame.evaluate("""() => {
                const body = document.body;
                const sel = window.getSelection();
                let refNode = body.lastChild;
                if (sel && sel.rangeCount > 0) {
                    refNode = sel.getRangeAt(0).endContainer;
                    if (refNode.nodeType === 3) refNode = refNode.parentNode;
                }
                const ins = document.createElement('ins');
                ins.className = 'adsbygoogle';
                ins.style.cssText = 'display:block;text-align:center;';
                ins.setAttribute('data-ad-layout', 'in-article');
                ins.setAttribute('data-ad-format', 'fluid');
                ins.setAttribute('data-ad-client', 'ca-pub-1646757278810260');
                ins.setAttribute('data-ad-slot', '3141593954');
                const script = document.createElement('script');
                script.async = true;
                script.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1646757278810260';
                script.setAttribute('crossorigin', 'anonymous');
                const pushScript = document.createElement('script');
                pushScript.textContent = '(adsbygoogle = window.adsbygoogle || []).push({});';
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
            ad_inserted = True
            print("[OK] 애드센스 DOM 삽입")
            time.sleep(0.5)
            continue

        frame.page.keyboard.type(stripped, delay=50)
        frame.page.keyboard.press("Enter")
        time.sleep(0.1)

    # TinyMCE 변경 반영
    page.evaluate("""() => {
        if (window.tinymce && tinymce.activeEditor) {
            tinymce.activeEditor.fire('change');
            tinymce.activeEditor.save();
        }
    }""")
    print("[OK] 본문 입력 완료")
    time.sleep(1)

    # 태그 입력
    tag_input = page.query_selector("#tagText")
    if tag_input:
        tag_input.click()
        for tag in tags:
            tag_input.fill("")
            tag_input.type(tag, delay=60)
            page.keyboard.press("Enter")
            time.sleep(0.5)
        print(f"[OK] 태그 입력: {tags}")

    time.sleep(1)

    # 임시저장 전 스크린샷
    page.screenshot(path=os.path.join(_DIR, "data", "test_v2_before_save.png"))

    # 임시저장 클릭
    url_before = page.url
    saved = page.evaluate("""() => {
        const buttons = document.querySelectorAll('button, a, input[type="button"]');
        for (const btn of buttons) {
            const text = btn.textContent.trim();
            if (text === '임시저장' || text.includes('임시저장')) {
                btn.click();
                return text;
            }
        }
        return null;
    }""")
    print(f"[임시저장 버튼] {saved}")
    time.sleep(5)

    url_after = page.url
    print(f"[URL 변경] {url_before} → {url_after}")

    # 저장 후 스크린샷
    page.screenshot(path=os.path.join(_DIR, "data", "test_v2_after_save.png"))

    # 성공 확인: 알림 메시지나 URL 변경 확인
    save_msg = page.evaluate("""() => {
        // 토스트나 알림 메시지 확인
        const toasts = document.querySelectorAll('[class*="toast"], [class*="alert"], [class*="notify"], [class*="snack"], .layer_post');
        for (const t of toasts) {
            if (t.textContent.trim()) return t.textContent.trim().substring(0, 100);
        }
        return null;
    }""")
    if save_msg:
        print(f"[알림 메시지] {save_msg}")

    # 3) 애드센스 텍스트 노출 확인 (현재 에디터에서)
    print("\n=== 애드센스 텍스트 노출 검사 ===")
    try:
        iframe_el2 = page.query_selector("#editor-tistory_ifr")
        if iframe_el2:
            frame2 = iframe_el2.content_frame()
            if frame2:
                body_html = frame2.evaluate("() => document.body.innerHTML")
                body_text = frame2.evaluate("() => document.body.innerText")

                has_ins = "adsbygoogle" in body_html
                has_script = "pagead2.googlesyndication" in body_html
                print(f"  HTML내 adsbygoogle <ins>: {'있음' if has_ins else '없음'}")
                print(f"  HTML내 adsense <script>: {'있음' if has_script else '없음'}")

                adsense_kw = ["adsbygoogle", "ca-pub-", "pagead2.googlesyndication", "data-ad-slot", "data-ad-client"]
                exposed = [kw for kw in adsense_kw if kw in body_text]
                if exposed:
                    print(f"  ⚠️  텍스트 노출: {exposed}")
                    for kw in exposed:
                        idx = body_text.find(kw)
                        s, e = max(0, idx-50), min(len(body_text), idx+len(kw)+50)
                        print(f"     → ...{body_text[s:e]}...")
                else:
                    print(f"  ✅ 텍스트 노출 없음 (정상)")
    except Exception as e:
        print(f"  확인 실패: {e}")

    # 4) 글 관리 페이지에서 임시저장 확인
    print("\n=== 임시저장 목록 확인 ===")
    page.goto("https://nolja100.tistory.com/manage/posts/", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)
    page.screenshot(path=os.path.join(_DIR, "data", "test_v2_post_list.png"))

    # 최신 글 목록에서 우리 글 찾기
    found = page.evaluate("""(title) => {
        const items = document.querySelectorAll('.item_post .tit_post, .item_post_title');
        for (const item of items) {
            if (item.textContent.includes(title.substring(0, 10))) {
                return item.textContent.trim().substring(0, 100);
            }
        }
        // 전체 페이지에서 제목 검색
        return document.body.innerText.includes(title.substring(0, 10)) ? 'FOUND_IN_PAGE' : 'NOT_FOUND';
    }""", title)
    print(f"  제목 검색 결과: {found}")

    browser.close()
    print("\n[완료]")
