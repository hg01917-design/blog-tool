#!/usr/bin/env python3
"""nolja100.tistory.com 임시저장 목록 + 애드센스 확인 v2"""
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

    # 1) 임시저장 목록 (type=0 = 임시저장)
    page.goto("https://nolja100.tistory.com/manage/posts/?type=0", wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)
    page.screenshot(path=os.path.join(_DIR, "data", "drafts_list_v2.png"), full_page=False)

    # 페이지 타이틀 / URL
    print(f"[URL] {page.url}")
    page_title = page.evaluate("() => document.title")
    print(f"[페이지 타이틀] {page_title}")

    # 목록 추출 시도 (여러 셀렉터)
    drafts = page.evaluate("""() => {
        // 테이블 형태
        let rows = document.querySelectorAll('#content .item_post_title, .post-item, .article-list tr');
        let result = [];
        // 일반적인 목록
        const allLinks = document.querySelectorAll('#content a[href*="/manage/post/"]');
        for (const a of allLinks) {
            const row = a.closest('tr') || a.closest('li') || a.closest('.item_post') || a.parentElement;
            const dateEl = row ? (row.querySelector('.date') || row.querySelector('td:last-child') || row.querySelector('.txt_date') || row.querySelector('time')) : null;
            result.push({
                title: a.textContent.trim().substring(0, 80),
                href: a.href,
                date: dateEl ? dateEl.textContent.trim() : '',
            });
        }
        if (result.length === 0) {
            // fallback: 모든 링크에서 제목 추출
            const links = document.querySelectorAll('a');
            for (const l of links) {
                if (l.href.includes('/manage/') && l.textContent.trim().length > 5) {
                    result.push({title: l.textContent.trim().substring(0, 80), href: l.href, date: ''});
                }
            }
        }
        return result.slice(0, 10);
    }""")

    print(f"\n=== 임시저장 목록 (최근 {len(drafts)}건) ===")
    target_link = None
    for i, d in enumerate(drafts, 1):
        marker = " ★" if "대릉원" in d.get("title", "") or "벚꽃" in d.get("title", "") else ""
        print(f"  {i}. {d['title']}  {d.get('date','')}{marker}")
        if "대릉원" in d.get("title", "") or ("벚꽃" in d.get("title", "") and "경주" in d.get("title", "")):
            target_link = d.get("href")

    if not drafts:
        print("  (목록 파싱 실패 - 스크린샷 확인)")

    # 2) 최신 글(또는 대릉원 글) 열어서 애드센스 확인
    if not target_link and drafts:
        target_link = drafts[0].get("href")

    if target_link:
        print(f"\n=== 글 열기: {target_link} ===")
        page.goto(target_link, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)
        page.screenshot(path=os.path.join(_DIR, "data", "draft_editor.png"), full_page=False)

        # TinyMCE iframe 내부 확인
        try:
            page.wait_for_selector("#editor-tistory_ifr", timeout=15000)
            iframe_el = page.query_selector("#editor-tistory_ifr")
            if iframe_el:
                frame = iframe_el.content_frame()
                if frame:
                    body_html = frame.evaluate("() => document.body.innerHTML")
                    body_text = frame.evaluate("() => document.body.innerText")

                    print(f"\n  [본문 HTML] {len(body_html)}자")
                    print(f"  [본문 텍스트] {len(body_text)}자")

                    # 애드센스 태그 확인
                    has_ins = "adsbygoogle" in body_html
                    has_script = "pagead2.googlesyndication" in body_html
                    print(f"  [HTML내 <ins> adsbygoogle] {'있음' if has_ins else '없음'}")
                    print(f"  [HTML내 <script> adsense] {'있음' if has_script else '없음'}")

                    # 텍스트 노출 확인
                    adsense_kw = ["adsbygoogle", "ca-pub-", "pagead2.googlesyndication", "data-ad-slot", "data-ad-client", "data-ad-format"]
                    exposed = [kw for kw in adsense_kw if kw in body_text]

                    if exposed:
                        print(f"\n  ⚠️  애드센스 코드 텍스트 노출 발견: {exposed}")
                        for kw in exposed:
                            idx = body_text.find(kw)
                            s, e = max(0, idx-40), min(len(body_text), idx+len(kw)+40)
                            print(f"     → ...{body_text[s:e]}...")
                    else:
                        print(f"\n  ✅ 애드센스 코드 텍스트 노출 없음 (정상)")

                    # 본문 텍스트 첫 200자 출력
                    print(f"\n  [본문 미리보기] {body_text[:200]}...")
        except Exception as e:
            print(f"  에디터 확인 실패: {e}")

    browser.close()
    print("\n완료.")
