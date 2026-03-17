#!/usr/bin/env python3
"""최종 테스트: 임시저장 + 에디터 내용 즉시 확인 (같은 세션)"""
import os, sys, json, time, re, random, logging, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 1) 글 생성 ──
from app import (
    claude_client, _get_model,
    SEO_PROMPTS_TRAVEL, PLATFORM_NAMES,
    ADSENSE_TISTORY, _insert_adsense_3,
    _auto_crawl_for_prompt,
)

keyword = "경주 벚꽃 대릉원"
use_model = _get_model()
system_prompt = SEO_PROMPTS_TRAVEL["tistory"]
system_prompt += "\n\n현재 연도는 2026년입니다."

crawled = _auto_crawl_for_prompt(keyword, "travel")
if crawled:
    system_prompt += f"\n\n[참고 자료]\n{crawled}"
    print(f"[크롤링] {len(crawled)}자")

system_prompt += (
    "\n\n[체험형 글쓰기 모드]\n"
    "- 1인칭 경험담, '~했어요', '~더라고요' 체 사용\n"
    "- 구체적인 사용 상황 생생하게 묘사"
)

# 제목+태그
meta_resp = claude_client.messages.create(
    model=use_model, max_tokens=500, system=system_prompt,
    messages=[{"role": "user", "content":
        f"키워드: {keyword}\n톤: 체험형\n카테고리: travel\n\n"
        "제목 1개와 태그 10개만.\n응답 형식:\n---제목---\n제목\n---태그---\n태그1,태그2,..."}],
)
meta = meta_resp.content[0].text.strip()
title = keyword
tags_str = ""
if "---제목---" in meta:
    p = meta.split("---제목---", 1)[1]
    if "---태그---" in p:
        title = p.split("---태그---")[0].strip().split("\n")[0].strip()
        tags_str = p.split("---태그---")[1].strip().split("\n")[0].strip()
print(f"[제목] {title}")
print(f"[태그] {tags_str}")

# 본문
body_resp = claude_client.messages.create(
    model=use_model, max_tokens=8000, system=system_prompt,
    messages=[{"role": "user", "content":
        f"키워드: {keyword}\n제목: {title}\n\n"
        "본문만 작성. 제목/태그 금지. HTML 금지. ##H2:소제목## 형식. 최소 1500자, 소제목 4-5개."}],
)
body = body_resp.content[0].text.strip()
body = re.sub(r'^```\w*\n?', '', body)
body = re.sub(r'\n?```$', '', body)
body = re.sub(r"\[이미지[^\]]*\]", "", body).strip()
body = f"<!-- 제목: {title} -->\n" + body
if ADSENSE_TISTORY:
    body = _insert_adsense_3(body, ADSENSE_TISTORY)

h2s = re.findall(r'##H2:(.+?)##', body)
print(f"[본문] {len(body)}자, H2 {len(h2s)}개")
tags_list = [t.strip() for t in tags_str.split(",") if t.strip()][:10]

# ── 2) 이미지 생성 ──
import tistory_playwright as tp
section_images = []
tmp_files = []
for h in h2s[:3]:
    try:
        en = tp._translate_to_english(h)
        path = tp._generate_imagen(en)
        section_images.append(path)
        tmp_files.append(path)
        print(f"[이미지] {h[:20]}... → OK")
    except Exception as e:
        print(f"[이미지] {h[:20]}... → 실패: {e}")

# ── 3) Playwright: 글쓰기 + 저장 + 즉시 검증 ──
cookie_path = os.path.join(_DIR, "data", "tistory_cookies_baremi542.json")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")
    with open(cookie_path, "r") as f:
        ctx.add_cookies(json.load(f))
    page = ctx.new_page()

    page.goto("https://nolja100.tistory.com/manage/newpost", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)
    if "accounts.kakao.com" in page.url or "auth/login" in page.url:
        print("[실패] 쿠키 만료"); browser.close(); sys.exit(1)

    # 복원팝업 닫기
    page.evaluate("""() => { document.querySelectorAll('button, a').forEach(b => {
        const t = b.textContent.trim();
        if (['새 글 작성','새로 작성','취소','아니오'].some(k => t.includes(k))) b.click();
    }); }""")
    time.sleep(1)

    # 에디터 대기
    page.wait_for_selector("#post-title-inp", timeout=15000)

    # 제목
    ti = page.query_selector("#post-title-inp")
    ti.click(); time.sleep(0.3)
    ti.type(title, delay=60)
    print("[OK] 제목 입력")

    # 본문 + 이미지
    tp._type_body_html(page, body, images=section_images)
    print("[OK] 본문 + 이미지 입력")

    # 태그
    tag_input = page.query_selector("#tagText")
    if tag_input:
        tag_input.click()
        for tag in tags_list:
            tag_input.fill(""); tag_input.type(tag, delay=60)
            page.keyboard.press("Enter"); time.sleep(0.5)
        print(f"[OK] 태그 {len(tags_list)}개")

    time.sleep(1)

    # ── 저장 전 스크린샷 ──
    page.screenshot(path=os.path.join(_DIR, "data", "final_before_save.png"))

    # 에디터 내 스크롤 → 본문 중간/하단 캡처
    page.evaluate("() => { const f = document.querySelector('#editor-tistory_ifr'); if(f) { f.contentWindow.scrollTo(0, 300); } }")
    time.sleep(0.5)
    page.screenshot(path=os.path.join(_DIR, "data", "final_body_scroll1.png"))
    page.evaluate("() => { const f = document.querySelector('#editor-tistory_ifr'); if(f) { f.contentWindow.scrollTo(0, 800); } }")
    time.sleep(0.5)
    page.screenshot(path=os.path.join(_DIR, "data", "final_body_scroll2.png"))

    # ── 에디터 내용 분석 (저장 전) ──
    iframe_el = page.query_selector("#editor-tistory_ifr")
    if iframe_el:
        frame = iframe_el.content_frame()
        if frame:
            bh = frame.evaluate("() => document.body.innerHTML")
            bt = frame.evaluate("() => document.body.innerText")
            imgs = bh.count("<img")
            h2s_ed = bh.count("<h2")
            print(f"\n=== 에디터 본문 분석 ===")
            print(f"  HTML: {len(bh)}자")
            print(f"  텍스트: {len(bt)}자")
            print(f"  이미지: {imgs}개")
            print(f"  H2: {h2s_ed}개")
            print(f"  애드센스 <ins>: {'있음' if 'adsbygoogle' in bh else '없음'}")
            adsense_exposed = [k for k in ["adsbygoogle","ca-pub-","pagead2"] if k in bt]
            if adsense_exposed:
                print(f"  ⚠️ 애드센스 텍스트 노출: {adsense_exposed}")
            else:
                print(f"  ✅ 애드센스 텍스트 노출 없음")
            if len(bt) >= 1500:
                print(f"  ✅ 분량 충분 ({len(bt)}자)")
            elif len(bt) >= 800:
                print(f"  ⚠️ 분량 보통 ({len(bt)}자)")
            else:
                print(f"  ❌ 분량 부족 ({len(bt)}자)")
            print(f"\n  [미리보기]\n  {bt[:500]}")

    # ── 임시저장 ──
    page.evaluate("""() => {
        const buttons = document.querySelectorAll('button');
        for (const b of buttons) {
            if (b.textContent.trim().includes('임시저장')) { b.click(); return true; }
        }
        return false;
    }""")
    time.sleep(3)
    page.screenshot(path=os.path.join(_DIR, "data", "final_after_save.png"))
    print("\n[OK] 임시저장 완료")

    browser.close()

# cleanup
for f in tmp_files:
    try: os.unlink(f)
    except: pass

print("[완료]")
