# Blog Tool - CLAUDE.md

## Project Overview
Flask + Gunicorn 블로그 자동화 도구 (서버: 158.247.206.99)

## Tech Stack
- Backend: Python 3.11, Flask, Gunicorn
- AI: Anthropic Claude API (Haiku/Sonnet)
- APIs: WordPress REST, Unsplash, Naver Search Ads, IndexNow, Google Indexing

## Key Files
- `app.py` — 메인 앱 (글 생성, WP 발행, 색인, 인증)
- `keywords.py` — 키워드 수집 블루프린트
- `orders.py` — 주문 알림 블루프린트
- `templates/` — Jinja2 HTML 템플릿

## Architecture
- Gunicorn (2 workers, port 5000) → Apache reverse proxy → app.baremi542.com
- 전역 인증: `@app.before_request` (세션 기반)
- `.env`에서 API 키/설정 로드 (thread-safe `_load_env_value`)
- `claude_client` (app.py 전역) — 다른 모듈에서 `from app import claude_client` 로 공유

## Keyword Module (keywords.py)
### 수집 소스
- Google 자동완성 (접미사 확장)
- Google 연관검색어 (한글 초성 + 알파벳 확장)
- Naver 자동완성 (API + 검색페이지 파싱)
- Naver 연관검색어

### 검색량 조회 (Naver Search Ads API)
- `_naver_ad_headers()` — HMAC-SHA256 서명 인증
- `_get_search_volume()` — 키워드 1개씩 조회 (공백 제거 필수)
- Rate limit: 요청 간 0.3초 딜레이, 429시 3초 대기
- `.env` 키: NAVER_AD_API_KEY, NAVER_AD_SECRET_KEY, NAVER_AD_CUSTOMER_ID

### AI 주제 추천
- `POST /api/keywords/suggest-topics` — Claude로 카테고리별 롱테일 주제 10개 추천
- `from app import claude_client, _get_model` 로 공유 클라이언트 사용

### Fallback 로직
- 수집 결과 < 5개 + 키워드 3단어 이상이면 `_shorten_keyword()`로 축약 후 재수집
- 불용어 제거 후 핵심 2~3단어 추출

## Conventions
- 카테고리: 여행, IT, 육아, 음식, 정부지원금, 재테크, 건강
- 정부지원금 카테고리: 공식 출처 기준, 2026년 기준 작성
- `.gitignore`: .env, __pycache__, *.log, *.bak, gunicorn.pid, .claude/, data/
- Git push 시 .env 포함 금지 (GitHub Push Protection 활성화)

## 2026-03-10 작업 내역
1. Git 초기화 + GitHub 원격 저장소 연결 (hg01917-design/blog-tool)
2. .gitignore 정리 (민감파일/임시파일 추적 제거)
3. 네이버 검색광고 API 연동 (키워드 월간 검색량 PC/모바일 조회)
4. 키워드 수집 페이지 전면 개편:
   - 7개 카테고리 + 시드 키워드 시스템
   - 검색량 테이블 (정렬, 필터, 체크박스, 바 차트)
   - AI 주제 추천 기능 (Claude API)
   - 롱테일 키워드 fallback (축약 재수집)
