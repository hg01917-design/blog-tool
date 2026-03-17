#!/usr/bin/env python3
"""임시저장 복원 → 에디터 내용 확인"""
import os, sys, json, time, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
from playwright.sync_api import sync_playwright

_DIR = os.path.dirname(os.path.abspath(__file__))
cookie_path = os.path.join(_DIR, "data", "tistory_cookies_baremi542.json")

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

    # 글쓰기 페이지 → 복원 팝업 뜸
    page.goto("https://nolja100.tistory.com/manage/newpost", wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)

    # 복원 팝업에서 "이어서 작성" 클릭
    restored = page.evaluate("""() => {
        const buttons = document.querySelectorAll('button, a');
        for (const btn of buttons) {
            const text = btn.textContent.trim();
            if (text.includes('이어서 작성') || text.includes('이전 글') || text.includes('불러오기')
                || text.includes('복원') || text.includes('확인') || text === '예') {
                btn.click();
                return text;
            }
        }
        return null;
    }""")
    print(f"[복원 팝업] {restored or '팝업 없음'}")
    time.sleep(3)

    # 스크린샷: 에디터 상단
    page.screenshot(path=os.path.join(_DIR, "data", "verify_editor_top.png"))

    # 에디터 내용 확인
    try:
        page.wait_for_selector("#editor-tistory_ifr", timeout=10000)
        iframe_el = page.query_selector("#editor-tistory_ifr")
        if iframe_el:
            frame = iframe_el.content_frame()
            if frame:
                body_html = frame.evaluate("() => document.body.innerHTML")
                body_text = frame.evaluate("() => document.body.innerText")

                img_count = body_html.count("<img")
                h2_count = body_html.count("<h2")
                figure_count = body_html.count("<figure")
                has_adsense_ins = "adsbygoogle" in body_html
                has_adsense_script = "pagead2.googlesyndication" in body_html

                print(f"\n=== 에디터 본문 분석 ===")
                print(f"  HTML 길이: {len(body_html)}자")
                print(f"  텍스트 길이: {len(body_text)}자")
                print(f"  이미지 <img>: {img_count}개")
                print(f"  <figure>: {figure_count}개")
                print(f"  <h2>: {h2_count}개")
                print(f"  애드센스 <ins>: {'있음' if has_adsense_ins else '없음'}")
                print(f"  애드센스 <script>: {'있음' if has_adsense_script else '없음'}")

                # 텍스트 노출 확인
                adsense_kw = ["adsbygoogle", "ca-pub-", "pagead2.googlesyndication"]
                exposed = [kw for kw in adsense_kw if kw in body_text]
                if exposed:
                    print(f"  ⚠️ 애드센스 텍스트 노출: {exposed}")
                else:
                    print(f"  ✅ 애드센스 텍스트 노출 없음")

                # 분량 판단
                if len(body_text) >= 1500:
                    print(f"  ✅ 분량 충분 ({len(body_text)}자)")
                elif len(body_text) >= 800:
                    print(f"  ⚠️ 분량 보통 ({len(body_text)}자)")
                else:
                    print(f"  ❌ 분량 부족 ({len(body_text)}자)")

                # 본문 미리보기
                print(f"\n  [미리보기] {body_text[:300]}...")

                # 제목 확인
                title_el = page.query_selector("#post-title-inp")
                if title_el:
                    title_val = title_el.input_value()
                    print(f"\n  [제목] {title_val}")

                # 태그 확인
                tag_els = page.query_selector_all(".tag_post .inner_tag .txt_tag")
                if tag_els:
                    tag_texts = [t.text_content().strip() for t in tag_els]
                    print(f"  [태그] {', '.join(tag_texts)}")

        # 스크롤해서 하단 캡처
        page.evaluate("() => { const ifr = document.querySelector('#editor-tistory_ifr'); if(ifr) ifr.scrollIntoView(); }")
        time.sleep(1)
        page.screenshot(path=os.path.join(_DIR, "data", "verify_editor_body.png"))

    except Exception as e:
        print(f"  에디터 확인 실패: {e}")

    browser.close()
    print("\n[완료]")
