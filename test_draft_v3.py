#!/usr/bin/env python3
"""경주 벚꽃 대릉원 - 이미지 포함 임시저장 + 검증 테스트 v3"""
import os, sys, json, time, re, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 1) 글 생성 (app.py의 generate 로직 재현) ──
from app import (
    claude_client, _get_model,
    SEO_PROMPTS_TRAVEL, PLATFORM_NAMES,
    ADSENSE_TISTORY, _insert_adsense_3,
    _auto_crawl_for_prompt, _search_unsplash_images,
)

keyword = "경주 벚꽃 대릉원"
platform = "tistory"
category = "travel"
tone = "experience"
use_model = _get_model()

seo_prompts = SEO_PROMPTS_TRAVEL
system_prompt = seo_prompts[platform]
system_prompt += f"\n\n현재 연도는 2026년입니다. 제목과 본문에 연도가 필요하면 반드시 이 연도를 사용하세요."

# 크롤링
crawled_info = _auto_crawl_for_prompt(keyword, category)
if crawled_info:
    system_prompt += (
        "\n\n[참고 자료 — 반드시 이 내용 기반으로 정확한 정보만 작성]\n"
        f"{crawled_info}\n\n"
        "위 참고 자료의 수치/날짜/조건을 우선 반영하되 그대로 복사하지 말고 자연스럽게 재구성하세요."
    )
    print(f"[크롤링] 참고 자료 {len(crawled_info)}자 수집")
else:
    print("[크롤링] 참고 자료 없음")

system_prompt += (
    "\n\n[체험형 글쓰기 모드]\n"
    "- 1인칭 경험담 형식으로 작성합니다\n"
    "- '제가 직접 해보니', '솔직히 말하면' 등의 표현을 자연스럽게 활용합니다\n"
    "- '~했습니다' 보다는 '~했어요', '~더라고요' 체를 사용합니다"
)

# 1-1) 제목+태그
meta_prompt = (
    f"다음 키워드로 {PLATFORM_NAMES[platform]} 블로그 글의 제목과 태그만 생성해주세요.\n\n"
    f"키워드: {keyword}\n"
    f"글 톤: 체험형 (1인칭 경험담, 솔직하고 생생한 톤)\n"
    f"카테고리: {category}\n\n"
    "요구사항:\n"
    "1. 제목은 SEO 최적화 (클릭률 높은 제목, 키워드 자연 포함)\n"
    "2. 태그는 10개 (쉼표 구분)\n\n"
    "응답 형식 (정확히):\n"
    "---제목---\n제목 텍스트\n---태그---\n태그1,태그2,...\n"
)
meta_resp = claude_client.messages.create(
    model=use_model, max_tokens=500,
    system=system_prompt,
    messages=[{"role": "user", "content": meta_prompt}],
)
meta_text = meta_resp.content[0].text.strip()
print(f"[메타 응답]\n{meta_text}\n")

title = keyword  # fallback
tags = ""
if "---제목---" in meta_text:
    parts = meta_text.split("---제목---", 1)[1]
    if "---태그---" in parts:
        title_part, tags_part = parts.split("---태그---", 1)
        title = title_part.strip().split("\n")[0].strip()
        tags = tags_part.strip().split("\n")[0].strip()

print(f"[제목] {title}")
print(f"[태그] {tags}")

# 1-2) 본문 생성
body_prompt = (
    f"키워드: {keyword}\n"
    f"제목: {title}\n\n"
    "위의 시스템 프롬프트에 정의된 말투, 도입부 패턴, 글 구조를 그대로 따라서 본문만 작성해줘.\n"
    "제목, 태그 포함하지 마. HTML 태그 사용 금지. ##H2:소제목## 형식 사용.\n"
    "최소 1500자 이상, 소제목 4-5개로 작성."
)
body_resp = claude_client.messages.create(
    model=use_model, max_tokens=8000,
    system=system_prompt,
    messages=[{"role": "user", "content": body_prompt}],
)
body = body_resp.content[0].text.strip()

# 후처리
body = re.sub(r'^```\w*\n?', '', body)
body = re.sub(r'\n?```$', '', body)
body = body.strip()
body = re.sub(r"\[이미지[^\]]*\]", "", body)
body = f"<!-- 제목: {title} -->\n" + body

# 애드센스 삽입
ad_code = ADSENSE_TISTORY
if ad_code:
    body = _insert_adsense_3(body, ad_code)

h2_count = len(re.findall(r'##H2:', body))
print(f"[본문] {len(body)}자, H2 소제목 {h2_count}개, ##AD## {'있음' if '##AD##' in body else '없음'}")

tags_list = [t.strip() for t in tags.split(",") if t.strip()][:10]

# ── 2) 티스토리 임시저장 (이미지 포함) ──
import tistory_playwright
result = tistory_playwright.publish_to_tistory(
    blog_id="nolja100",
    title=title,
    body_html=body,
    tags=tags_list,
    account_id="baremi542",
)
print(f"\n[발행 결과] {json.dumps(result, ensure_ascii=False, indent=2)}")

# ── 3) 임시저장 후 에디터에서 이미지/분량 확인 ──
if result.get("success"):
    print("\n=== 에디터 내용 확인 ===")
    from playwright.sync_api import sync_playwright
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

        # 글 관리 페이지에서 최신 글 확인
        page.goto("https://nolja100.tistory.com/manage/posts/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        page.screenshot(path=os.path.join(_DIR, "data", "v3_post_list.png"))

        # 최신 글 찾기
        first_link = page.evaluate("""(title) => {
            const links = document.querySelectorAll('#content a');
            for (const a of links) {
                if (a.href.includes('/manage/post/') || a.href.includes('/manage/newpost/')) {
                    if (a.textContent.includes(title.substring(0, 8))) return a.href;
                }
            }
            // fallback: 첫 번째 글
            for (const a of links) {
                if (a.href.includes('/manage/post/')) return a.href;
            }
            return null;
        }""", title)

        if first_link:
            print(f"  글 열기: {first_link}")
            page.goto(first_link, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            page.screenshot(path=os.path.join(_DIR, "data", "v3_editor_top.png"))

            # 스크롤 다운해서 전체 캡처
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            time.sleep(1)
            page.screenshot(path=os.path.join(_DIR, "data", "v3_editor_mid.png"))

            try:
                page.wait_for_selector("#editor-tistory_ifr", timeout=10000)
                iframe_el = page.query_selector("#editor-tistory_ifr")
                if iframe_el:
                    frame = iframe_el.content_frame()
                    if frame:
                        body_html = frame.evaluate("() => document.body.innerHTML")
                        body_text = frame.evaluate("() => document.body.innerText")

                        img_count = body_html.count("<img")
                        h2_in_editor = body_html.count("<h2")
                        print(f"  본문 HTML: {len(body_html)}자")
                        print(f"  본문 텍스트: {len(body_text)}자")
                        print(f"  이미지 <img> 태그: {img_count}개")
                        print(f"  H2 태그: {h2_in_editor}개")

                        # 애드센스 텍스트 노출 확인
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
            except Exception as e:
                print(f"  에디터 확인 실패: {e}")

        browser.close()

print("\n[완료]")
