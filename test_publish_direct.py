#!/usr/bin/env python3
"""publish_to_tistory() 직접 호출 테스트 - 마크다운 H2 + 이미지"""
import os, sys, json, re, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from app import (
    claude_client, _get_model,
    SEO_PROMPTS_TRAVEL, ADSENSE_TISTORY, _insert_adsense_3,
    _auto_crawl_for_prompt,
)

keyword = "경주 벚꽃 대릉원"
use_model = _get_model()
system_prompt = SEO_PROMPTS_TRAVEL["tistory"]
system_prompt += "\n\n현재 연도는 2026년입니다."
system_prompt += ("\n\n[체험형 글쓰기 모드]\n"
    "- 1인칭 경험담, '~했어요', '~더라고요' 체 사용")

crawled = _auto_crawl_for_prompt(keyword, "travel")
if crawled:
    system_prompt += f"\n\n[참고 자료]\n{crawled}"

# 제목+태그+본문 한번에
resp = claude_client.messages.create(
    model=use_model, max_tokens=8000, system=system_prompt,
    messages=[{"role": "user", "content":
        f"키워드: {keyword}\n\n"
        "아래 형식으로 제목, 태그, 본문을 작성해줘.\n\n"
        "---제목---\n제목 텍스트\n---태그---\n태그1,태그2,...(10개)\n---본문---\n"
        "본문 내용 (##H2:소제목## 형식 필수, 소제목 4-5개, 1500자 이상)\n"
        "HTML 태그 사용 금지. ##H2:소제목## 형식만 사용."}],
)
text = resp.content[0].text.strip()
print(f"[AI 응답] {len(text)}자")

title = keyword
tags_str = ""
body = text

if "---제목---" in text and "---본문---" in text:
    parts = text.split("---제목---", 1)[1]
    if "---태그---" in parts:
        t_part, rest = parts.split("---태그---", 1)
        title = t_part.strip().split("\n")[0].strip()
        if "---본문---" in rest:
            tags_str, body = rest.split("---본문---", 1)
            tags_str = tags_str.strip().split("\n")[0].strip()
            body = body.strip()

body = re.sub(r'^```\w*\n?', '', body)
body = re.sub(r'\n?```$', '', body)
body = re.sub(r"\[이미지[^\]]*\]", "", body).strip()
body = f"<!-- 제목: {title} -->\n" + body
if ADSENSE_TISTORY:
    body = _insert_adsense_3(body, ADSENSE_TISTORY)

# H2 확인 (두 가지 형식)
h2_marker = len(re.findall(r'##H2:', body))
h2_md = len(re.findall(r'^##\s+', body, re.MULTILINE))
print(f"[제목] {title}")
print(f"[태그] {tags_str}")
print(f"[본문] {len(body)}자, ##H2: {h2_marker}개, md## {h2_md}개")

tags_list = [t.strip() for t in tags_str.split(",") if t.strip()][:10]

# publish_to_tistory 직접 호출
import tistory_playwright
result = tistory_playwright.publish_to_tistory(
    blog_id="nolja100",
    title=title,
    body_html=body,
    tags=tags_list,
    account_id="baremi542",
)
print(f"\n[결과] {json.dumps(result, ensure_ascii=False, indent=2)}")
