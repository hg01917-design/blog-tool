#!/usr/bin/env python3
"""경주 벚꽃 대릉원 키워드 임시저장 테스트 스크립트"""
import os, sys, json, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

import anthropic
client = anthropic.Anthropic()

keyword = "경주 벚꽃 대릉원"

# 1) 제목 생성
title_resp = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=200,
    messages=[{"role": "user", "content": f"다음 키워드로 여행 블로그 글 제목 1개만 작성해줘. 다른 말 없이 제목만.\n키워드: {keyword}"}],
)
title = title_resp.content[0].text.strip().strip('"')
print(f"[제목] {title}")

# 2) 본문 생성
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

# 3) 티스토리 임시저장
import tistory_playwright
result = tistory_playwright.publish_to_tistory(
    blog_id="nolja100",
    title=title,
    body_html=body,
    tags=tags,
    account_id="baremi542",
)
print(f"\n[결과] {json.dumps(result, ensure_ascii=False, indent=2)}")
