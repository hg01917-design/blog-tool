#!/usr/bin/env python3
"""nolja100.tistory.com 임시저장 목록 확인 + 애드센스 텍스트 노출 검사"""
import os, sys, json, time, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from playwright.sync_api import sync_playwright

cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tistory_cookies_baremi542.json")

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

    # 1) 임시저장 목록 확인
    page.goto("https://nolja100.tistory.com/manage/posts?type=saved", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    # 스크린샷 저장
    page.screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "drafts_list.png"))

    # 임시저장 글 목록 추출
    drafts = page.evaluate("""() => {
        const rows = document.querySelectorAll('.list_post .item_post, table tbody tr, .post-item, [class*="post-list"] li');
        const result = [];
        for (const row of rows) {
            const titleEl = row.querySelector('a, .title, .tit_post, td:nth-child(2)');
            const dateEl = row.querySelector('.date, time, td:last-child, .txt_date');
            result.push({
                title: titleEl ? titleEl.textContent.trim().substring(0, 80) : '(no title)',
                date: dateEl ? dateEl.textContent.trim() : '',
            });
        }
        return result;
    }""")

    print("\\n=== 임시저장 목록 ===")
    if drafts:
        for i, d in enumerate(drafts[:10], 1):
            print(f"  {i}. {d['title']}  [{d['date']}]")
    else:
        print("  (목록을 파싱하지 못함 - 스크린샷으로 확인)")

    # 2) 최신 임시저장 글 열어서 애드센스 텍스트 노출 확인
    first_link = page.evaluate("""() => {
        const link = document.querySelector('.list_post .item_post a, table tbody tr a, .post-item a');
        return link ? link.href : null;
    }""")

    if first_link:
        print(f"\\n=== 최신 글 확인: {first_link} ===")
        page.goto(first_link, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        page.screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draft_detail.png"))

        # TinyMCE iframe 내부 HTML 추출
        try:
            page.wait_for_selector("#editor-tistory_ifr", timeout=10000)
            iframe_el = page.query_selector("#editor-tistory_ifr")
            if iframe_el:
                frame = iframe_el.content_frame()
                if frame:
                    body_html = frame.evaluate("() => document.body.innerHTML")
                    body_text = frame.evaluate("() => document.body.innerText")

                    # 애드센스 코드가 텍스트로 노출되는지 확인
                    adsense_keywords = ["adsbygoogle", "ca-pub-", "pagead2.googlesyndication", "data-ad-slot", "data-ad-client"]
                    text_exposed = []
                    for kw in adsense_keywords:
                        if kw in body_text:
                            text_exposed.append(kw)

                    print(f"\\n  본문 HTML 길이: {len(body_html)}자")
                    print(f"  본문 텍스트 길이: {len(body_text)}자")

                    # HTML에 애드센스 태그 존재 여부
                    has_ins = "<ins" in body_html and "adsbygoogle" in body_html
                    has_script = "pagead2.googlesyndication" in body_html
                    print(f"  애드센스 <ins> 태그: {'있음' if has_ins else '없음'}")
                    print(f"  애드센스 <script> 태그: {'있음' if has_script else '없음'}")

                    if text_exposed:
                        print(f"\\n  ⚠️ 애드센스 코드가 텍스트로 노출됨: {text_exposed}")
                        # 노출된 부분 컨텍스트 출력
                        for kw in text_exposed:
                            idx = body_text.find(kw)
                            start = max(0, idx - 30)
                            end = min(len(body_text), idx + len(kw) + 30)
                            print(f"     컨텍스트: ...{body_text[start:end]}...")
                    else:
                        print(f"\\n  ✅ 애드센스 코드가 텍스트로 노출되지 않음 (정상)")
        except Exception as e:
            print(f"  에디터 확인 실패: {e}")
    else:
        print("\\n  최신 글 링크를 찾지 못함 - 스크린샷으로 확인")

    browser.close()
