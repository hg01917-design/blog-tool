import os
import re
import sys
import base64
import secrets
import json
import random
from datetime import datetime
from typing import Optional
import requests as http_requests
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash
import anthropic

# app.py가 있는 디렉토리를 sys.path에 추가 (CWD 무관하게 import 보장)
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

load_dotenv(os.path.join(_APP_DIR, ".env"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["SESSION_COOKIE_NAME"] = "wordpress_autoblog"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true"

# ──────────────────────────────────────────────
#  인증 설정
# ──────────────────────────────────────────────
_ENV_PATH = os.path.join(_APP_DIR, ".env")


AVAILABLE_MODELS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _load_env_value(key, default=""):
    """`.env` 파일에서 특정 키 값을 읽어온다. (멀티워커 안전)"""
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1]
    return default


def _get_model():
    """현재 설정된 AI 모델을 반환."""
    val = _load_env_value("AI_MODEL", DEFAULT_MODEL)
    return val if val in AVAILABLE_MODELS.values() else DEFAULT_MODEL


def _save_env_value(key, value):
    """`.env` 파일에서 특정 키를 업데이트한다."""
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = []
    lines = [l for l in lines if not l.startswith(key + "=")]
    lines.append(f"{key}={value}\n")
    with open(_ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _load_password_from_env():
    """`.env` 파일에서 비밀번호 설정을 매번 읽어온다. (멀티워커 안전)"""
    pw_hash = ""
    pw_plain = ""
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("APP_PASSWORD_HASH="):
                    pw_hash = line.split("=", 1)[1]
                elif line.startswith("APP_PASSWORD="):
                    pw_plain = line.split("=", 1)[1]
    return pw_hash, pw_plain


def login_required(f):
    """로그인 안 된 요청을 /login으로 리다이렉트하는 데코레이터."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"error": "로그인이 필요합니다"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("authenticated"):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if _check_password(password):
            session["authenticated"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "비밀번호가 틀렸습니다"

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


def _check_password(password: str) -> bool:
    """`.env`에서 비밀번호를 읽어서 비교. 해시 우선, 평문 폴백."""
    if not password:
        return False
    pw_hash, pw_plain = _load_password_from_env()
    if pw_hash:
        return check_password_hash(pw_hash, password)
    if pw_plain:
        return secrets.compare_digest(password, pw_plain)
    return False


def _update_env_password(new_password: str):
    """`.env` 파일의 비밀번호를 해시로 교체."""
    new_hash = generate_password_hash(new_password)

    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = []

    # 기존 APP_PASSWORD / APP_PASSWORD_HASH 줄 제거
    lines = [l for l in lines if not l.startswith("APP_PASSWORD_HASH=") and not l.startswith("APP_PASSWORD=")]

    # 해시 방식으로 저장 (평문 제거)
    lines.append(f"APP_PASSWORD_HASH={new_hash}\n")

    with open(_ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    result = None
    action = request.form.get("action", "")

    if request.method == "POST" and action == "change_password":
        current = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not _check_password(current):
            result = {"ok": False, "msg": "현재 비밀번호가 틀렸습니다"}
        elif len(new_pw) < 4:
            result = {"ok": False, "msg": "새 비밀번호는 4자 이상이어야 합니다"}
        elif new_pw != confirm:
            result = {"ok": False, "msg": "새 비밀번호가 일치하지 않습니다"}
        else:
            _update_env_password(new_pw)
            result = {"ok": True, "msg": "비밀번호가 변경되었습니다"}

    elif request.method == "POST" and action == "change_model":
        model_key = request.form.get("ai_model", "")
        if model_key in AVAILABLE_MODELS:
            _save_env_value("AI_MODEL", AVAILABLE_MODELS[model_key])
            result = {"ok": True, "msg": f"모델이 {model_key.upper()}로 변경되었습니다"}
        else:
            result = {"ok": False, "msg": "잘못된 모델입니다"}

    current_model = _get_model()
    return render_template("settings.html", result=result,
                           current_model=current_model, models=AVAILABLE_MODELS)


# 주문 관리 Blueprint 등록
from orders import orders_bp
app.register_blueprint(orders_bp)

# 키워드 수집 Blueprint 등록
from keywords import keywords_bp
app.register_blueprint(keywords_bp)


@app.before_request
def require_login():
    """로그인/정적 파일 외 모든 요청에 인증 강제."""
    allowed = ("login_page", "static", "indexnow_key_file")
    if request.endpoint in allowed:
        return
    if not session.get("authenticated"):
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            from flask import abort
            abort(401)
        return redirect(url_for("login_page"))


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

claude_client = anthropic.Anthropic()
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

# WordPress REST API 설정
WP_URL = os.environ.get("WP_URL", "").rstrip("/")
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")

# 네이버 블로그 설정
NAVER_BLOG_ID = os.environ.get("NAVER_BLOG_ID", "")

# 티스토리 블로그 설정 (쉼표 구분)
TISTORY_BLOGS = [b.strip() for b in os.environ.get("TISTORY_BLOGS", "goodisak").split(",") if b.strip()]

# IndexNow 설정
INDEXNOW_KEY = os.environ.get("INDEXNOW_KEY", "")
if not INDEXNOW_KEY:
    INDEXNOW_KEY = secrets.token_hex(16)

# Google Indexing API 설정
GOOGLE_CREDENTIALS_PATH = os.environ.get(
    "GOOGLE_CREDENTIALS_PATH", "/home/master/blog-tool/google-credentials.json"
)

# AdSense: 파일에서 읽기 (여러 줄 HTML 지원)
_adsense_file = os.environ.get("ADSENSE_FILE", "")
ADSENSE_CODE = ""
if _adsense_file:
    _adsense_path = os.path.join(os.path.dirname(__file__), _adsense_file)
    if os.path.exists(_adsense_path):
        with open(_adsense_path, "r", encoding="utf-8") as f:
            ADSENSE_CODE = f.read().strip()

# 카테고리별 AdSense 코드
ADSENSE_IT = (
    '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1646757278810260"\n'
    '     crossorigin="anonymous"></script>\n'
    '<ins class="adsbygoogle"\n'
    '     style="display:inline-block;width:300px;height:250px"\n'
    '     data-ad-client="ca-pub-1646757278810260"\n'
    '     data-ad-slot="3141593954"></ins>\n'
    '<script>\n'
    '     (adsbygoogle = window.adsbygoogle || []).push({});\n'
    '</script>'
)

ADSENSE_TRAVEL = (
    '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1646757278810260"\n'
    '     crossorigin="anonymous"></script>\n'
    '<ins class="adsbygoogle"\n'
    '     style="display:block"\n'
    '     data-ad-client="ca-pub-1646757278810260"\n'
    '     data-ad-slot="3113682298"\n'
    '     data-ad-format="auto"\n'
    '     data-full-width-responsive="true"></ins>\n'
    '<script>\n'
    '     (adsbygoogle = window.adsbygoogle || []).push({});\n'
    '</script>'
)

# ──────────────────────────────────────────────
#  티스토리 통합 프롬프트 (주제 자동 감지)
# ──────────────────────────────────────────────
TISTORY_UNIFIED_PROMPT = (
    "당신은 티스토리 블로그 전문 콘텐츠 제작자이자 애드센스 수익화 전문가입니다.\n"
    "티스토리는 구글 검색과 다음(Daum) 검색에 노출되는 플랫폼입니다.\n\n"
    "[1단계: 주제 자동 감지]\n"
    "키워드를 분석하여 아래 3가지 중 하나로 자동 판단하고, 해당 톤앤매너를 적용하세요.\n"
    "- 여행/맛집/숙소 → 따뜻하고 1인칭 체험형 ('~더라고요', '~했어요' 체, 감성 묘사)\n"
    "- IT/테크/앱/코딩 → 명확하고 단계별 설명형 ('~합니다' 체, 스크린샷 대체 설명, 비교표)\n"
    "- 정보성(지원금/정책/생활꿀팁) → 사실 중심, 수치 필수, 출처 명시 ('~입니다' 체)\n\n"
    "[2단계: 구글/다음 SEO 규칙]\n"
    "- 제목: 28~35자, 핵심 키워드 앞쪽 배치, 숫자 권장\n"
    "- H2 소제목: 반드시 질문형 (예: '비용은 얼마일까?', '어떻게 신청하나요?')\n"
    "- 각 H2 아래 첫 문장: 40~60자 직답형 (구글 Featured Snippet 대응)\n"
    "- 본문 첫 100자 이내에 핵심 키워드 자연 삽입\n"
    "- h2, h3 태그로 명확한 계층 구조 (구글 크롤러 최적화)\n"
    "- 구조화된 목록, 표, 정의형 문장 활용 (Featured Snippet 노출 극대화)\n"
    "- 메타 설명: 150~160자로 별도 생성 (구글/다음 검색 결과 미리보기용)\n"
    "- 태그: 10~15개 (쉼표 구분)\n"
    "- 분량: 2,500~4,000자\n\n"
    "[3단계: 필수 구조]\n"
    "- 도입부: 핵심 정보 요약 박스 (배경색 div, 3~5줄)\n"
    "- 본론: H2 소제목 3~5개, 각 섹션에 구체적 내용\n"
    "- 표: HTML table 태그로 최소 1개 이상 (비교표, 요약표 등)\n"
    "- FAQ 섹션: Q&A 3~4개 필수\n"
    "- 마무리: 자연스러운 1~2문장 (요약 리스트 금지)\n\n"
    "[4단계: 절대 금지 표현]\n"
    "- '마무리 요약', '핵심 정리', '결론부터 말할게요'\n"
    "- '이 글을 찾으셨다면', '도움이 됐다면 구독·댓글'\n"
    "- '안녕하세요, 오늘은 ~에 대해 알아보겠습니다'\n"
    "- '지금 바로 확인하세요!', '필독!', '꼭 알아야 할'\n"
    "- 과장 표현('최고', '완전', '무조건', '혁신적')\n"
    "- AI가 쓴 느낌이 나는 모든 정형화된 도입·마무리 패턴\n"
    "- 경험하지 않은 것을 경험한 척 위장\n\n"
    "[5단계: 기술 금지]\n"
    "- JSON-LD, Schema Markup, <script type=\"application/ld+json\"> 코드 절대 금지\n"
    "- 광고 플레이스홀더(<!-- 광고위치 -->, [광고], Advertisement div) 절대 금지\n"
    "- 광고는 자동 삽입되므로 본문에 광고 관련 코드를 넣지 마세요\n"
    "- h1, h2, h3에 CSS 스타일 지정 금지 (스킨에서 처리됨)\n"
    "- 이미지는 자동 삽입되므로 [이미지] 플레이스홀더 금지\n\n"
    "HTML 형식으로 작성합니다."
)

# 티스토리 광고 코드 (슬롯: 3113682298)
ADSENSE_TISTORY = (
    '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1646757278810260"\n'
    '     crossorigin="anonymous"></script>\n'
    '<ins class="adsbygoogle"\n'
    '     style="display:block"\n'
    '     data-ad-client="ca-pub-1646757278810260"\n'
    '     data-ad-slot="3113682298"\n'
    '     data-ad-format="auto"\n'
    '     data-full-width-responsive="true"></ins>\n'
    '<script>\n'
    '     (adsbygoogle = window.adsbygoogle || []).push({});\n'
    '</script>'
)

# IT 카테고리 티스토리 프롬프트
TISTORY_IT_PROMPT = (
    "당신은 IT/테크 전문 티스토리 블로그 콘텐츠 제작자이자 애드센스 수익화 전문가입니다.\n"
    "티스토리는 구글 검색과 다음(Daum) 검색에 노출되는 플랫폼입니다.\n\n"
    "[톤앤매너]\n"
    "- 명확하고 단계별 설명형 ('~합니다' 체)\n"
    "- 스크린샷 대체 설명, 비교표 적극 활용\n"
    "- 기술 용어는 풀어서 설명하되 전문성 유지\n\n"
    "[구글/다음 SEO 규칙]\n"
    "- 제목: 28~35자, 핵심 키워드 앞쪽 배치, 숫자 권장\n"
    "- H2 소제목: 반드시 질문형 (예: '비용은 얼마일까?', '어떻게 설정하나요?')\n"
    "- 각 H2 아래 첫 문장: 40~60자 직답형 (구글 Featured Snippet 대응)\n"
    "- 본문 첫 100자 이내에 핵심 키워드 자연 삽입\n"
    "- h2, h3 태그로 명확한 계층 구조 (구글 크롤러 최적화)\n"
    "- 구조화된 목록, 표, 정의형 문장 활용 (Featured Snippet 노출 극대화)\n"
    "- 메타 설명: 150~160자로 별도 생성 (구글/다음 검색 결과 미리보기용)\n"
    "- 태그: 10~15개 (쉼표 구분)\n"
    "- 분량: 2,500~4,000자\n\n"
    "[필수 구조]\n"
    "- 도입부: 핵심 정보 요약 박스 (배경색 div, 3~5줄)\n"
    "- 본론: H2 소제목 3~5개, 각 섹션에 구체적 내용\n"
    "- 표: HTML table 태그로 최소 1개 이상 (비교표, 스펙표 등)\n"
    "- FAQ 섹션: Q&A 3~4개 필수\n"
    "- 마무리: 자연스러운 1~2문장 (요약 리스트 금지)\n\n"
    "[절대 금지]\n"
    "- '마무리 요약', '핵심 정리', '결론부터 말할게요'\n"
    "- '안녕하세요, 오늘은 ~에 대해 알아보겠습니다'\n"
    "- 과장 표현('최고', '완전', '무조건', '혁신적')\n"
    "- AI가 쓴 느낌이 나는 모든 정형화된 도입·마무리 패턴\n"
    "- JSON-LD, Schema Markup, <script type=\"application/ld+json\"> 코드\n"
    "- 광고 플레이스홀더(<!-- 광고위치 -->, [광고], Advertisement div)\n"
    "- h1, h2, h3에 CSS 스타일 지정 금지 (스킨에서 처리됨)\n"
    "- 이미지는 자동 삽입되므로 [이미지] 플레이스홀더 금지\n\n"
    "HTML 형식으로 작성합니다."
)

# 여행 카테고리 티스토리 프롬프트
TISTORY_TRAVEL_PROMPT = (
    "당신은 여행 전문 티스토리 블로그 콘텐츠 제작자이자 애드센스 수익화 전문가입니다.\n"
    "티스토리는 구글 검색과 다음(Daum) 검색에 노출되는 플랫폼입니다.\n\n"
    "[톤앤매너]\n"
    "- 따뜻하고 1인칭 체험형 ('~더라고요', '~했어요' 체)\n"
    "- 감성 묘사와 실용 정보를 자연스럽게 결합\n"
    "- 독자가 직접 여행을 계획할 수 있도록 구체적 정보 제공\n\n"
    "[구글/다음 SEO 규칙]\n"
    "- 제목: 28~35자, 핵심 키워드 앞쪽 배치, 숫자 권장\n"
    "- H2 소제목: 반드시 질문형 (예: '입장료는 얼마일까?', '가는 방법은?')\n"
    "- 각 H2 아래 첫 문장: 40~60자 직답형 (구글 Featured Snippet 대응)\n"
    "- 본문 첫 100자 이내에 핵심 키워드 자연 삽입\n"
    "- h2, h3 태그로 명확한 계층 구조 (구글 크롤러 최적화)\n"
    "- 구조화된 목록, 표, 정의형 문장 활용 (Featured Snippet 노출 극대화)\n"
    "- 메타 설명: 150~160자로 별도 생성 (구글/다음 검색 결과 미리보기용)\n"
    "- 태그: 10~15개 (쉼표 구분)\n"
    "- 분량: 2,500~4,000자\n\n"
    "[필수 구조]\n"
    "- 도입부: 핵심 정보 요약 박스 (배경색 div, 3~5줄)\n"
    "- 본론: H2 소제목 3~5개, 각 섹션에 구체적 내용\n"
    "- 표: HTML table 태그로 최소 1개 이상 (요금표, 일정표 등)\n"
    "- FAQ 섹션: Q&A 3~4개 필수\n"
    "- 마무리: 자연스러운 1~2문장 (요약 리스트 금지)\n\n"
    "[절대 금지]\n"
    "- '마무리 요약', '핵심 정리', '결론부터 말할게요'\n"
    "- '안녕하세요, 오늘은 ~에 대해 알아보겠습니다'\n"
    "- 여행자보험, 해외카드, 유심/eSIM, 항공권 최저가, 면세 가이드 주제\n"
    "- 과장 표현('최고', '완전', '무조건', '혁신적')\n"
    "- AI가 쓴 느낌이 나는 모든 정형화된 도입·마무리 패턴\n"
    "- JSON-LD, Schema Markup, <script type=\"application/ld+json\"> 코드\n"
    "- 광고 플레이스홀더(<!-- 광고위치 -->, [광고], Advertisement div)\n"
    "- h1, h2, h3에 CSS 스타일 지정 금지 (스킨에서 처리됨)\n"
    "- 이미지는 자동 삽입되므로 [이미지] 플레이스홀더 금지\n\n"
    "HTML 형식으로 작성합니다."
)

# 정부지원금 카테고리 티스토리 프롬프트
TISTORY_GOVERNMENT_PROMPT = (
    "당신은 정부지원금·복지 정책 전문 티스토리 블로그 콘텐츠 제작자이자 애드센스 수익화 전문가입니다.\n"
    "티스토리는 구글 검색과 다음(Daum) 검색에 노출되는 플랫폼입니다.\n\n"
    "[톤앤매너]\n"
    "- 사실 중심, 수치 필수, 출처 명시 ('~입니다' 체)\n"
    "- 복지로, 정부24 등 공식 사이트 정보 기반\n"
    "- 행정 용어를 일반인이 바로 이해할 수 있도록 풀어서 설명\n"
    "- YMYL 콘텐츠 특성상 정확성과 신뢰도를 최우선\n\n"
    "[구글/다음 SEO 규칙]\n"
    "- 제목: 28~35자, 핵심 키워드 앞쪽 배치, 숫자 권장\n"
    "- H2 소제목: 반드시 질문형 (예: '신청 자격은?', '얼마나 받을 수 있나요?')\n"
    "- 각 H2 아래 첫 문장: 40~60자 직답형 (구글 Featured Snippet 대응)\n"
    "- 본문 첫 100자 이내에 핵심 키워드 자연 삽입\n"
    "- h2, h3 태그로 명확한 계층 구조 (구글 크롤러 최적화)\n"
    "- 구조화된 목록, 표, 정의형 문장 활용 (Featured Snippet 노출 극대화)\n"
    "- 메타 설명: 150~160자로 별도 생성 (구글/다음 검색 결과 미리보기용)\n"
    "- 태그: 10~15개 (쉼표 구분)\n"
    "- 분량: 2,500~4,000자\n\n"
    "[필수 구조]\n"
    "- 도입부: 정책 요약 박스 (대상/금액/신청기간/신청방법, 배경색 div)\n"
    "- 본론: H2 소제목 3~5개 (신청 자격, 지원 금액, 신청 방법, 필요 서류 등)\n"
    "- 표: HTML table 태그로 최소 2개 (지원 금액표, 신청 일정표 등)\n"
    "- FAQ 섹션: Q&A 3~4개 필수\n"
    "- 마무리: 자연스러운 1~2문장 (요약 리스트 금지)\n\n"
    "[필수 포함 정보]\n"
    "- 지원 대상 (연령, 소득 기준 등)\n"
    "- 지원 금액 (구체적 수치)\n"
    "- 신청 기간 (날짜)\n"
    "- 신청 방법 (온라인/오프라인 경로)\n"
    "- 필요 서류\n\n"
    "[절대 금지]\n"
    "- '마무리 요약', '핵심 정리', '결론부터 말할게요'\n"
    "- '안녕하세요, 오늘은 ~에 대해 알아보겠습니다'\n"
    "- '무조건 받을 수 있다', '꼭 신청하세요' 등 과장 표현\n"
    "- 확인되지 않은 금액이나 날짜 추측\n"
    "- 특정 대행 업체나 유료 서비스 홍보\n"
    "- JSON-LD, Schema Markup, <script type=\"application/ld+json\"> 코드\n"
    "- 광고 플레이스홀더(<!-- 광고위치 -->, [광고], Advertisement div)\n"
    "- h1, h2, h3에 CSS 스타일 지정 금지 (스킨에서 처리됨)\n"
    "- 이미지는 자동 삽입되므로 [이미지] 플레이스홀더 금지\n\n"
    "HTML 형식으로 작성합니다."
)

# IT 카테고리 SEO 프롬프트
SEO_PROMPTS_IT = {
    "tistory": TISTORY_IT_PROMPT,
    "naver": (
        "당신은 네이버 블로그 SEO 전문가입니다. "
        "네이버 블로그의 특성을 잘 이해하고 있습니다:\n"
        "- 네이버 검색 알고리즘(C-Rank, D.I.A.)에 최적화된 글을 작성합니다\n"
        "- 제목에 핵심 키워드를 앞쪽에 배치합니다\n"
        "- 본문 첫 문단에 핵심 키워드를 포함하고, 전체적으로 키워드를 자연스럽게 반복합니다\n"
        "- 소제목, 볼드체, 구분선 등을 활용해 가독성을 높입니다\n"
        "- 글 길이는 1800~3000자 사이가 적정합니다\n"
        "- 경험과 정보가 결합된 톤으로 작성합니다 (체험형 + 정보형)\n"
        "- 마지막에 해시태그 형식으로 관련 키워드 5~10개를 추가합니다\n"
        "- HTML 형식으로 작성합니다"
    ),
    "wordpress": (
        "당신은 워드프레스 블로그 SEO 전문가입니다. "
        "워드프레스와 구글 검색 최적화를 잘 이해하고 있습니다:\n"
        "- 구글 검색엔진 최적화(Google SEO)를 최우선으로 합니다\n"
        "- Yoast SEO 기준에 맞는 구조로 작성합니다\n"
        "- h2, h3, h4 태그로 명확한 계층 구조를 만듭니다\n"
        "- 메타 디스크립션용 요약문(150~160자)을 별도로 제공합니다\n"
        "- 내부 링크와 외부 링크 배치 포인트를 제안합니다\n"
        "- 글 길이는 2000~3500자 사이가 적정합니다\n"
        "- Featured Snippet에 노출될 수 있도록 목록, 표, 정의형 문장을 활용합니다\n"
        "- JSON-LD, Schema Markup 코드는 절대 포함하지 마세요\n"
        "- HTML 형식으로 작성합니다"
    ),
    "blogspot": (
        "당신은 블로그스팟(Blogger) SEO 전문가입니다. "
        "블로그스팟과 구글 검색 최적화를 잘 이해하고 있습니다:\n"
        "- 구글 검색엔진에 직접 최적화되는 구글 자체 플랫폼의 이점을 활용합니다\n"
        "- 제목에 핵심 키워드를 포함하고, URL 슬러그도 키워드 기반으로 제안합니다\n"
        "- h2, h3 태그를 활용한 구조화된 글을 작성합니다\n"
        "- 본문 초반에 핵심 키워드를 배치하고, 이미지 alt 텍스트 제안을 포함합니다\n"
        "- 글 길이는 1500~2500자 사이가 적정합니다\n"
        "- 라벨(Label) 제안을 3~5개 포함합니다\n"
        "- AdSense 친화적인 문단 길이와 구조를 유지합니다\n"
        "- HTML 형식으로 작성합니다"
    ),
}

# 여행 카테고리 SEO 프롬프트 (티스토리 전용)
SEO_PROMPTS_TRAVEL = {
    "tistory": TISTORY_TRAVEL_PROMPT,
    "naver": (
        "당신은 여행 전문 네이버 블로그 SEO 전문가입니다.\n"
        "- 네이버 검색 알고리즘(C-Rank, D.I.A.)에 최적화된 여행 글을 작성합니다\n"
        "- 제목에 핵심 키워드를 앞쪽에 배치합니다\n"
        "- 본문 첫 문단에 핵심 키워드를 포함하고, 전체적으로 키워드를 자연스럽게 반복합니다\n"
        "- 소제목, 볼드체, 구분선 등을 활용해 가독성을 높입니다\n"
        "- 글 길이는 1800~3000자 사이가 적정합니다\n"
        "- 실제 여행 경험 느낌의 톤으로 작성합니다 (체험형 + 정보형)\n"
        "- 마지막에 해시태그 형식으로 관련 키워드 5~10개를 추가합니다\n"
        "- 여행자보험, 해외카드, 유심/eSIM, 항공권 최저가, 면세 가이드 주제는 절대 금지\n"
        "- HTML 형식으로 작성합니다"
    ),
    "wordpress": (
        "당신은 여행 전문 워드프레스 블로그 SEO 전문가입니다.\n"
        "- 구글 검색엔진 최적화(Google SEO)를 최우선으로 합니다\n"
        "- Yoast SEO 기준에 맞는 구조로 작성합니다\n"
        "- h2, h3, h4 태그로 명확한 계층 구조를 만듭니다\n"
        "- 메타 디스크립션용 요약문(150~160자)을 별도로 제공합니다\n"
        "- 글 길이는 2000~3500자 사이가 적정합니다\n"
        "- Featured Snippet에 노출될 수 있도록 목록, 표, 정의형 문장을 활용합니다\n"
        "- 여행자보험, 해외카드, 유심/eSIM, 항공권 최저가, 면세 가이드 주제는 절대 금지\n"
        "- HTML 형식으로 작성합니다"
    ),
}

# 살림/생활 카테고리 SEO 프롬프트 (네이버 블로그 전용 - 퇴근후 살림)
SEO_PROMPTS_LIVING = {
    "naver": (
        "당신은 '퇴근후살림' 블로그를 운영하는 38세 워킹맘 하린입니다.\n"
        "지방 광역시에 살고, 회사를 다니면서 세 아이(초등/유치원/어린이집)를 키우고 있어요.\n"
        "고정지출 줄이는 게 취미이자 숙제. 전기세/가스비/통신비/장보기 직접 아껴본 것들과 정부지원금 신청 경험을 정리합니다.\n\n"
        "[직업 노출 규칙]\n"
        "- 기본값: 직업 언급 하지 않음\n"
        "- 허용되는 경우만 드러낼 것:\n"
        "  1. 연말정산, 세금, 가계부 등 재무/회계와 직접 연관될 때\n"
        "  2. '숫자에 밝은 편이라' 정도의 간접적 암시만 허용\n"
        "- 금지: '회계팀 다니면서', '직장에서 회계 업무를 하다 보니' 등 직접 언급\n"
        "- 직장인임은 드러낼 수 있음 (퇴근 후, 야근, 월급날 등 맥락)\n\n"
        "[성격]\n"
        "- 완벽주의 아님. 되는 것만 빠르게 챙기는 스타일\n"
        "- 귀찮아도 돈 되면 찾아서 함. 근데 솔직하게 씀\n"
        "- 과장 없음. 효과 없었으면 없었다고 씀\n"
        "- 직접 해본 것: '해봤더니', '신청해봤는데'\n"
        "- 직접 못 해본 것: '찾아보니', '후기들 보니', '알아봤는데'\n\n"
        "[말투]\n"
        "어미: ~더라고요, ~거든요, ~했는데, ~이에요 (~합니다 절대 금지)\n"
        "ㅠㅠ, ㅎㅎ 이모티콘 자연스럽게 1~2개\n"
        "솔직히 말하면, 생각보다, 알고 보니, 저도 몰랐는데 → 자주 사용\n\n"
        "[역할]\n"
        "- 네이버 검색 알고리즘(C-Rank, D.I.A.)에 최적화된 생활 정보 전문가\n"
        "- 워킹맘 현실 공감 + 실용 정보를 결합한 체험형 콘텐츠 제작\n"
        "- 트래픽 키워드와 고단가 CPC 키워드를 자연스럽게 연결\n\n"
        "[글 사양]\n"
        "- 분량: 1,500~2,500자\n"
        "- 제목: 40자 내외, 숫자+연도+감성 키워드 포함, 핵심 키워드 앞쪽 배치\n"
        "- 제목에 대출·보험·지원금 단어 절대 금지 (YMYL 방지)\n"
        "- 롱테일 메인 키워드를 첫 100자 이내에 자연 삽입\n"
        "- H2 소제목: 전부 질문형 필수, 롱테일 키워드 포함\n"
        "  ❌ '신청 방법' → ✅ '신청 방법, 어떻게 하면 될까요?'\n"
        "  ❌ '할인 금액' → ✅ '실제로 얼마나 절약되는 걸까요?'\n"
        "- 각 H2 아래 핵심 답변 40~60자 직접 제시\n"
        "- 비교 내용은 반드시 HTML table 태그로 작성\n"
        "- 구체적 수치(금액/kWh 등) 최소 3개 이상\n"
        "- 태그: 20개 (인기 태그 10개 + 세부 틈새 태그 10개)\n\n"
        "[본문 구조]\n"
        "1. 도입부 (3~5줄): 워킹맘 현실 공감 멘트로 시작\n"
        "   → 패턴 A: 상황 공감 ('퇴근하고 고지서 보는 순간 한숨부터 나왔어요...')\n"
        "   → 패턴 B: 직접 경험 계기 ('지난달에 직접 해봤는데 이게 진짜 되더라고요')\n"
        "   → 패턴 C: 비용 공감 ('한 달 통신비 계산해봤더니 세 아이 합쳐서 얼마인지...')\n\n"
        "[도입부 시작 패턴 - 매번 다르게, 아래 중 하나 선택]\n"
        "절대 고지서/청구서/한숨 패턴 반복 금지. 아래처럼 다양하게:\n"
        "- 정보 발견형: '이거 알고 계셨어요? 저는 최근에야 알았는데 꽤 쏠쏠하더라고요.'\n"
        "- 주변 사례형: '주변에서 이거 신청했다는 얘기 듣고 저도 찾아봤어요.'\n"
        "- 실수 공유형: '작년에 이거 몰라서 그냥 넘겼는데, 올해는 꼭 챙기려고요.'\n"
        "- 계기형: '아이 학교 엄마한테 듣고 바로 검색해봤어요.'\n"
        "- 의심형: '반신반의하면서 해봤는데 진짜 되더라고요.'\n"
        "- 비교형: '다른 방법이랑 비교해봤는데 이게 제일 간편했어요.'\n"
        "- 시즌형: '이맘때쯤 꼭 챙겨야 하는 거라서 정리해봤어요.'\n\n"
        "2. 소제목 섹션 3~4개 (질문형)\n"
        "3. 생활 할인 혜택 언급 (본문 중간 1~2줄)\n"
        "   → 예: '참고로 기초생활수급자나 차상위계층이라면 한전 복지할인으로 월 최대 16,000원 추가 절약도 가능해요.'\n"
        "   → 신청 방법 상세 안내는 하지 말 것\n"
        "4. 체험담 박스 1~2개 ('직접 해봤더니' 형식, 3~5줄)\n"
        "5. FAQ 섹션 - Q&A 3개\n"
        "6. 마무리 (3~5줄): 핵심 1~2줄 요약(수치 포함) + 공감/응원 + '공감♥ 댓글 이웃추가 환영해요'\n\n"
        "[말투 패턴 - 이 느낌으로]\n"
        "- '그 찝찝함 아시죠ㅠㅠ' → 공감형 도입\n"
        "- '설마 진짜 되겠어? 했는데 이게 진짜 되더라구요' → 반신반의 후 경험 증언\n"
        "- '막상 해보니 10분도 안 걸렸어요' → 독자 안심\n"
        "- '그냥 그대로 입력하면 끝이더라구요' → 쉽다는 걸 직접 보여줌\n"
        "- '퇴근하고 지쳐서 귀찮은 날도 많은데ㅠㅠ' → 독자 상황 공감\n"
        "어미: ~더라구요, ~거든요, ~했는데, ~이에요 (딱딱한 ~합니다 금지)\n"
        "ㅠㅠ, ㅎㅎ 이모티콘 자연스럽게 1~2개\n\n"
        "[체험 표현 패턴 - 3회 이상 반드시 사용]\n"
        "[네이버 모바일 가독성 - 필수]\n"
        "- 한 문단은 2~3줄 이내로 짧게\n"
        "- 문단 사이 줄바꿈 자주 사용\n"
        "- 긴 내용은 잘게 쪼개서 나눠서 써\n"
        "- 한 문단에 한 가지 내용만\n"
        "- 리스트 나열할 때는 번호나 줄바꿈으로 구분\n"
        "- 정확한 수치 사용 ('약간', '적당히' 최소화)\n\n"
        "[문단 길이 예시 - 반드시 이 형식으로]\n"
        "❌ 나쁜 예 (절대 금지):\n"
        "다른 방법이랑 비교해봤는데 이게 제일 간편했어요. 회계팀 다니면서 세 아이 챙기다 보니 퇴근하고 한숨이 먼저 나와요ㅠㅠ "
        "아이들 밥 준비하면서 '오늘은 뭘 만들지' 고민하는 순간이 제일 힘들더라고요. "
        "그래서 지난해부터 퇴근 후 정확히 30분 안에 만들 수 있는 반찬 3가지를 집중적으로 연습했어요. "
        "이제는 그걸 루틴처럼 돌리는데, 솔직히 이 방법이 제일 실행 가능했어요.\n\n"
        "✅ 좋은 예 (반드시 이 형식):\n"
        "다른 방법이랑 비교해봤는데 이게 제일 간편했어요.\n\n"
        "회계팀 다니면서 세 아이 챙기다 보니, 퇴근하고 한숨부터 나와요.\n\n"
        "'오늘은 뭘 만들지' — 이 고민이 제일 힘들더라고요.\n\n"
        "그래서 지난해부터 딱 30분 루틴을 만들었어요.\n\n"
        "[이미지 가이드]\n"
        "- 이미지는 자동 삽입되므로 [이미지] 플레이스홀더를 넣지 마세요\n"
        "- AI 이미지 프롬프트를 별도로 제공하지 마세요\n\n"
        "[절대 금지]\n"
        "- JSON-LD, Schema Markup, <script type=\"application/ld+json\"> 코드 절대 금지\n"
        "- '무조건 절약됩니다' / '이것만 알면 됩니다' (과장·허위)\n"
        "- '○○마트 ○○제품 구매 추천' (특정 상품 홍보)\n"
        "- '안녕하세요', '오늘은 ~에 대해 알아보겠습니다' (AI 티)\n"
        "- 대출·보험·정부보조금 신청 안내 (YMYL 위험)\n"
        "- 광고 플레이스홀더(<!-- 광고위치 -->, [광고], Advertisement div)\n\n"
        "HTML 형식으로 작성합니다."
    ),
}

# 정부지원금/정책 카테고리 SEO 프롬프트
SEO_PROMPTS_GOVERNMENT = {
    "wordpress": (
        "당신은 정부지원금 정보를 쉽게 풀어쓰는 워드프레스 블로그 작가입니다.\n"
        "독자는 지원금이 필요한 평범한 사람들입니다.\n\n"
        "[역할]\n"
        "- 복지로, 정부24, 고용노동부 등 공식 사이트 정보를 기반으로 정확한 글을 작성\n"
        "- 구글 검색엔진 최적화(Google SEO)를 최우선으로 합니다\n"
        "- 행정 용어를 일반인이 바로 이해할 수 있도록 풀어서 설명\n"
        "- YMYL 콘텐츠 특성상 정확성과 신뢰도를 최우선\n\n"
        "[글쓰기 규칙]\n"
        "- 첫 문장은 독자 공감으로 시작 ('요즘 ~때문에 알아보시는 분들 많죠.')\n"
        "- 정보는 자연스러운 문장으로 풀어서 서술 (나열식 금지)\n"
        "- 수치는 구체적으로 ('최대 200만원', '만 34세 이하' 등)\n"
        "- 마무리는 짧고 담백하게, 요약 리스트 없이 1~2문장\n\n"
        "[글 사양]\n"
        "- 분량: 2,500~3,500자\n"
        "- 제목: 30~40자, 핵심 키워드 + 연도 + '신청방법/조건/대상' 포함\n"
        "- H2 소제목: 질문형 필수 (예: '신청 자격은?', '얼마나 받을 수 있나요?')\n"
        "- 각 H2 아래 첫 문장 40~60자 직답형\n"
        "- 표: HTML table 태그로 최소 2개 (지원 금액표, 신청 일정표 등)\n"
        "- 정책 요약 박스: 도입부 상단 필수 (대상, 금액, 신청기간, 신청방법)\n"
        "- Q&A 섹션: 3~4개\n"
        "- 태그: 10~15개\n\n"
        "[필수 포함 정보]\n"
        "- 지원 대상 (연령, 소득 기준 등)\n"
        "- 지원 금액 (구체적 수치)\n"
        "- 신청 기간 (날짜)\n"
        "- 신청 방법 (온라인/오프라인 경로)\n"
        "- 필요 서류\n\n"
        "[절대 금지]\n"
        "- '~에 대해 알아보겠습니다', '~를 정리해드리겠습니다'\n"
        "- '마무리 요약', '핵심 정리', '총정리', '결론적으로', '정리하자면'\n"
        "- '도움이 되셨으면 좋겠습니다'\n"
        "- 번호 리스트로 끝내는 마무리\n"
        "- '무조건 받을 수 있다', '꼭 신청하세요' 등 과장 표현\n"
        "- 특정 대행 업체나 유료 서비스 홍보\n"
        "- 확인되지 않은 금액이나 날짜 추측\n"
        "- JSON-LD, Schema Markup 코드\n"
        "- 광고 플레이스홀더\n\n"
        "HTML 형식으로 작성합니다."
    ),
    "tistory": TISTORY_GOVERNMENT_PROMPT,
    "naver": (
        "당신은 '퇴근후 살림' 블로그를 운영하는 워킹맘 블로거입니다.\n"
        "정부지원금/복지 정책 정보를 직접 찾아보고 신청해본 경험을 바탕으로 씁니다.\n\n"
        "[말투 - 이 느낌으로]\n"
        "- '이거 놓치면 진짜 아깝더라구요' → 공감으로 시작\n"
        "- '직접 신청해봤는데 이게 진짜 되더라구요' → 경험자 증언\n"
        "- '막상 해보니 10분도 안 걸렸어요' → 독자 안심\n"
        "- '그냥 그대로 입력하면 끝이더라구요' → 쉽다는 걸 직접 보여줌\n"
        "- '퇴근하고 지쳐서 귀찮은 날도 많은데ㅠㅠ' → 독자 상황 공감\n"
        "어미: ~더라구요, ~거든요, ~했는데, ~이에요 (딱딱한 ~합니다 금지)\n"
        "ㅠㅠ, ㅎㅎ 이모티콘 자연스럽게 1~2개\n\n"
        "[도입부 4단계]\n"
        "1. 공감 (독자 상황 먼저)\n"
        "2. 나도 같은 입장 ('저도 찾아봤는데')\n"
        "3. 발견 ('생각보다 ~하더라고요')\n"
        "4. 본문 연결 ('그래서 하나씩 정리해볼게요')\n"
        "도입부 금지: '~이란 무엇인가요?', '오늘은 ~알아볼게요'\n\n"
        "[글 구조]\n"
        "최상단: 핵심 요약 박스 (대상/금액/신청기간/방법 4가지)\n"
        "H2-1: 신청 방법 단계별\n"
        "H2-2: 자격 조건 (신청 안 되는 경우 반드시 포함)\n"
        "H2-3: 얼마 받을 수 있나 (시뮬레이션)\n"
        "H2-4: 자주 묻는 질문 Q&A\n\n"
        "[글쓰기 규칙]\n"
        "- 분량: 1500자 이상\n"
        "- 수치/날짜는 정확하게, 모르면 '공식 사이트 확인 필요'로 표기\n"
        "- 직접 경험한 척 과장 금지\n"
        "- 마무리 금지: '정리하자면', '도움이 되셨으면', '이상으로'\n\n"
        "[광고 삽입 - 필수]\n"
        "##AD## 태그 2개: 도입부 끝 직후, 마무리 문단 바로 앞\n\n"
        "[이미지]\n"
        "##IMG:이미지설명## 태그 1~2곳\n\n"
        "[출력 형식]\n"
        "첫 줄: 제목만 (순수 텍스트) / ##H2:소제목## 형식 / HTML 태그 금지\n"
        "##AD##, ##IMG:설명## 태그는 그대로 유지\n"
    ),
}

# 정부지원금 글 하단 면책 문구
GOVERNMENT_DISCLAIMER = (
    '<div style="margin-top:2em;padding:1.2em;background:#f8f9fa;border:1px solid #dee2e6;border-radius:8px;font-size:0.9em;color:#666;">'
    '<strong>※ 안내사항</strong><br>'
    '이 글은 정보 제공 목적으로 작성되었으며, 정확한 내용은 '
    '<a href="https://www.bokjiro.go.kr" target="_blank" rel="noopener">복지로</a>, '
    '<a href="https://www.gov.kr" target="_blank" rel="noopener">정부24</a> 등 '
    '공식 사이트에서 반드시 확인하시기 바랍니다. '
    '정책 내용은 변경될 수 있으므로 신청 전 최신 정보를 확인하세요.'
    '</div>'
)

PLATFORM_NAMES = {
    "tistory": "티스토리",
    "naver": "네이버 블로그",
    "wordpress": "워드프레스",
    "blogspot": "블로그스팟",
}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/blog")
def blog_page():
    return render_template("dashboard.html")


@app.route("/blog/queue")
def queue_page():
    return render_template("queue.html")


@app.route("/blog/write")
def write_page():
    platform = request.args.get("platform", "tistory")
    if platform not in ("tistory", "naver", "wordpress"):
        platform = "tistory"
    return render_template("write.html", platform=platform, current_model=_get_model())


@app.route("/shop")
def shop_page():
    return render_template("section.html",
        title="쇼핑몰 자동화",
        title_en="Shop Automation",
        icon_class="purple",
        icon_svg='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 002 1.61h9.72a2 2 0 002-1.61L23 6H6"/></svg>',
        desc="쇼핑몰 상품 등록 · 재고 관리 · 가격 자동화<br>곧 추가될 예정입니다")


@app.route("/work")
def work_page():
    return render_template("section.html",
        title="업무 자동화",
        title_en="Work Automation",
        icon_class="blue",
        icon_svg='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
        desc="반복 업무 자동화 · 문서 생성 · 보고서 작성<br>곧 추가될 예정입니다")


@app.route("/personal")
def personal_page():
    return render_template("section.html",
        title="개인",
        title_en="Personal",
        icon_class="dark",
        icon_svg='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
        desc="개인 메모 · 일정 관리 · 맞춤 도구<br>곧 추가될 예정입니다")


@app.route("/write")
def write():
    platform = request.args.get("platform", "tistory")
    if platform not in ("tistory", "naver", "wordpress"):
        platform = "tistory"
    return render_template("write.html", platform=platform, current_model=_get_model())


@app.route("/api/unsplash-search")
def unsplash_search():
    """프론트엔드 이미지 교체용 Unsplash 검색 API."""
    query = request.args.get("q", "").strip()
    if not query or not UNSPLASH_ACCESS_KEY:
        return jsonify({"results": []})
    try:
        resp = http_requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "orientation": "landscape", "per_page": 9},
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return jsonify({"results": []})
        items = resp.json().get("results", [])
        results = [
            {
                "thumb": r["urls"]["small"],
                "full": r["urls"]["raw"] + "?w=800&fm=webp&q=80",
                "alt": r.get("alt_description") or query,
                "credit": r["user"]["name"],
            }
            for r in items
        ]
        return jsonify({"results": results})
    except Exception:
        return jsonify({"results": []})


def _strip_html(html_text: str) -> str:
    """HTML에서 스크립트/스타일 제거 후 텍스트만 추출합니다."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _scrape_naver_blog(url: str) -> dict:
    """네이버 블로그 URL에서 제목, 소제목, 본문 텍스트를 구조화하여 추출합니다."""
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0"
    result = {"title": "", "headings": [], "body": "", "url": url}
    try:
        resp = http_requests.get(url, timeout=15, headers={"User-Agent": UA})
        resp.raise_for_status()
        html = resp.text

        # 네이버 블로그 iframe 구조 대응 — PostView로 진입
        iframe_match = re.search(r'src="(https://blog\.naver\.com/PostView\.naver[^"]+)"', html)
        if iframe_match:
            iframe_resp = http_requests.get(iframe_match.group(1), timeout=15, headers={"User-Agent": UA})
            iframe_resp.raise_for_status()
            html = iframe_resp.text

        # 제목 추출
        title_match = re.search(r'<div[^>]*class="[^"]*se-title-text[^"]*"[^>]*>(.*?)</div>', html, flags=re.DOTALL)
        if not title_match:
            title_match = re.search(r'<h3[^>]*class="[^"]*se_textarea[^"]*"[^>]*>(.*?)</h3>', html, flags=re.DOTALL)
        if not title_match:
            title_match = re.search(r'<title[^>]*>(.*?)</title>', html, flags=re.DOTALL)
        if title_match:
            result["title"] = _strip_html(title_match.group(1)).strip()

        # 본문 영역 추출 — se-main-container (스마트에디터 ONE) 우선
        body_html = ""
        # 방법 1: se-main-container 전체 (중첩 div 대응을 위해 넉넉하게 캡처)
        main_match = re.search(
            r'(<div[^>]*class="[^"]*se-main-container[^"]*"[^>]*>)',
            html, flags=re.DOTALL
        )
        if main_match:
            start = main_match.start()
            # se-main-container 시작부터 문서 끝까지에서 본문 추출
            body_html = html[start:start + 100000]
        else:
            # 방법 2: postViewArea (구 에디터)
            pva_match = re.search(
                r'(<div[^>]*id="postViewArea"[^>]*>)',
                html, flags=re.DOTALL
            )
            if pva_match:
                body_html = html[pva_match.start():pva_match.start() + 100000]

        if not body_html:
            body_html = html

        # 소제목 추출 (h2/h3/se-section-title 등)
        headings = []
        # 스마트에디터 ONE 소제목
        for m in re.finditer(r'<div[^>]*class="[^"]*se-section-title[^"]*"[^>]*>(.*?)</div>', body_html, flags=re.DOTALL):
            h = _strip_html(m.group(1)).strip()
            if h and len(h) > 2:
                headings.append(h)
        # 일반 h2/h3 태그
        if not headings:
            for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', body_html, flags=re.DOTALL):
                h = _strip_html(m.group(1)).strip()
                if h and len(h) > 2:
                    headings.append(h)
        # strong/b 태그로 된 소제목 (볼드 텍스트가 한 줄에 단독으로 있는 경우)
        if not headings:
            for m in re.finditer(r'<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>', body_html, flags=re.DOTALL):
                h = _strip_html(m.group(1)).strip()
                if 5 <= len(h) <= 50:
                    headings.append(h)
                if len(headings) >= 10:
                    break

        result["headings"] = headings[:10]
        result["body"] = _strip_html(body_html)[:5000]
        return result
    except Exception as e:
        print(f"[크롤링 실패] {url}: {e}")
        return result


@app.route("/api/crawl-competitors", methods=["POST"])
def crawl_competitors():
    """키워드로 네이버 블로그 상위 5개를 크롤링하여 팩트를 추출합니다."""
    data = request.get_json()
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "키워드를 입력해주세요."}), 400

    # 네이버 블로그 검색 API (openapi 없이 검색페이지 파싱)
    try:
        search_url = "https://search.naver.com/search.naver"
        resp = http_requests.get(search_url, params={
            "where": "blog", "query": keyword, "sm": "tab_opt"
        }, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

        # 개별 포스팅 URL만 추출 (username/postid 형식, 상위 5개)
        # blog.naver.com/username/숫자 패턴만 매칭 (블로그 홈 URL 제외)
        urls = re.findall(r'href="(https://blog\.naver\.com/[^"/]+/\d+)"', resp.text)
        # 중복 제거하면서 순서 유지
        seen = set()
        unique_urls = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)
            if len(unique_urls) >= 5:
                break

        if not unique_urls:
            return jsonify({"error": "네이버 블로그 검색 결과가 없습니다.", "results": []}), 200

    except Exception as e:
        return jsonify({"error": f"네이버 검색 실패: {str(e)}"}), 400

    # 각 블로그 크롤링 (구조화된 데이터)
    results = []
    all_contents = []
    for i, blog_url in enumerate(unique_urls):
        scraped = _scrape_naver_blog(blog_url)
        if scraped["body"]:
            heading_text = ""
            if scraped["headings"]:
                heading_text = " | 소제목: " + ", ".join(scraped["headings"])
            all_contents.append(
                f"[글 {i+1}] 제목: {scraped['title']}{heading_text}\n"
                f"본문: {scraped['body'][:2000]}"
            )
            results.append({
                "url": blog_url,
                "title": scraped["title"],
                "headings": scraped["headings"],
                "length": len(scraped["body"]),
            })

    if not all_contents:
        return jsonify({"error": "블로그 본문을 가져올 수 없습니다.", "results": []}), 200

    # AI로 소제목 구조 + 팩트 통합 추출
    combined = "\n\n".join(all_contents)
    try:
        fact_resp = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            system="당신은 경쟁 블로그 분석 전문가입니다. 여러 블로그 글의 구조와 팩트를 정밀하게 분석합니다.",
            messages=[{"role": "user", "content": (
                f"'{keyword}' 키워드로 검색한 네이버 블로그 상위 글 {len(all_contents)}개를 분석해주세요.\n\n"
                f"{combined[:10000]}\n\n"
                "아래 형식으로 분석 결과를 작성하세요.\n\n"
                "---소제목 구조---\n"
                "상위 글들이 공통으로 사용하는 소제목/섹션 구조를 정리하세요.\n"
                "- 각 글의 소제목 패턴을 비교하여 공통 구조를 도출\n"
                "- 우리 글에 적용할 추천 소제목 구조 (H2 5~7개) 제안\n\n"
                "---팩트---\n"
                "글들에서 추출한 구체적 사실 정보를 카테고리별로 나열하세요.\n"
                "- 날짜/기간: (영업시간, 운영기간, 신청기한, 방문 시기 등)\n"
                "- 장소/위치: (주소, 교통편, 주차 정보 등)\n"
                "- 가격/비용: (입장료, 이용료, 메뉴 가격, 할인 등)\n"
                "- 연락처/링크: (전화번호, 공식 사이트, 예약 링크 등)\n"
                "- 조건/자격: (신청자격, 필요서류, 제한사항 등)\n"
                "- 수치/통계: (면적, 수용인원, 평점, 후기 수 등)\n"
                "- 실전 팁: (블로거들이 공통으로 추천하는 팁/주의사항)\n"
                "※ 각 항목에 출처 글 번호를 [글1][글3] 형태로 표기\n"
                "※ 여러 글에서 수치가 다르면 범위로 표기 (예: 5,000~8,000원)\n\n"
                "---요약---\n"
                "- 상위 글들의 공통 주제와 핵심 메시지\n"
                "- 우리 글에서 차별화할 수 있는 포인트"
            )}],
        )
        result_text = fact_resp.content[0].text.strip()

        headings_analysis = ""
        facts = ""
        summary = ""

        if "---소제목 구조---" in result_text:
            after_headings = result_text.split("---소제목 구조---", 1)[1]
            if "---팩트---" in after_headings:
                headings_analysis, rest = [p.strip() for p in after_headings.split("---팩트---", 1)]
                if "---요약---" in rest:
                    facts, summary = [p.strip() for p in rest.split("---요약---", 1)]
                else:
                    facts = rest.strip()
            else:
                headings_analysis = after_headings.strip()
        elif "---팩트---" in result_text:
            after_facts = result_text.split("---팩트---", 1)[1]
            if "---요약---" in after_facts:
                facts, summary = [p.strip() for p in after_facts.split("---요약---", 1)]
            else:
                facts = after_facts.strip()

        return jsonify({
            "headings_analysis": headings_analysis,
            "facts": facts,
            "summary": summary,
            "results": results,
            "count": len(all_contents),
        })
    except anthropic.APIError as e:
        return jsonify({"error": f"팩트 추출 실패: {e.message}"}), 500


@app.route("/api/extract-facts", methods=["POST"])
def extract_facts():
    """경쟁 블로그 글(URL 또는 텍스트)에서 날짜/장소/가격 등 팩트를 AI로 추출합니다."""
    data = request.get_json()
    content = data.get("content", "").strip()
    url = data.get("url", "").strip()

    if not content and not url:
        return jsonify({"error": "경쟁글 URL 또는 내용을 입력해주세요."}), 400

    # URL이 주어지면 본문 스크래핑
    if url and not content:
        if "blog.naver.com" in url:
            scraped = _scrape_naver_blog(url)
            content = scraped["body"]
        else:
            try:
                resp = http_requests.get(url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                resp.raise_for_status()
                content = _strip_html(resp.text)[:5000]
            except Exception as e:
                return jsonify({"error": f"URL 스크래핑 실패: {str(e)}"}), 400

    if not content:
        return jsonify({"error": "본문 내용을 가져올 수 없습니다."}), 400

    try:
        fact_resp = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system="당신은 블로그 글에서 팩트(사실 정보)를 추출하는 전문가입니다. 정확한 정보만 추출하세요.",
            messages=[{"role": "user", "content": (
                "다음 블로그 글에서 팩트 정보를 추출해주세요.\n\n"
                f"글 내용:\n{content[:5000]}\n\n"
                "추출 항목 (있는 것만):\n"
                "- 날짜/기간 (영업시간, 운영기간, 신청기한 등)\n"
                "- 장소/위치 (주소, 교통편 등)\n"
                "- 가격/비용 (입장료, 이용료, 할인 등)\n"
                "- 연락처/링크 (전화번호, 공식 사이트 등)\n"
                "- 조건/자격 (신청자격, 필요서류 등)\n"
                "- 수치/통계 (면적, 수용인원, 평점 등)\n\n"
                "응답 형식:\n"
                "---팩트---\n"
                "각 항목을 '- 카테고리: 내용' 형식으로 나열\n"
                "---요약---\n"
                "이 글의 핵심 주제를 1~2문장으로 요약"
            )}],
        )
        result_text = fact_resp.content[0].text.strip()

        facts = ""
        summary = ""
        if "---팩트---" in result_text:
            after_facts = result_text.split("---팩트---", 1)[1]
            if "---요약---" in after_facts:
                facts, summary = [p.strip() for p in after_facts.split("---요약---", 1)]
            else:
                facts = after_facts.strip()

        return jsonify({"facts": facts, "summary": summary})
    except anthropic.APIError as e:
        return jsonify({"error": f"팩트 추출 실패: {e.message}"}), 500


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    keyword = data.get("keyword", "").strip()
    platform = data.get("platform", "tistory")
    tone = data.get("tone", "informative")
    category = data.get("category", "it")
    subtype = data.get("subtype", "")
    competitor_facts = data.get("competitor_facts", "").strip()

    # 프론트에서 선택한 모델 (없으면 설정 페이지 기본값)
    req_model = data.get("model", "")
    use_model = req_model if req_model in AVAILABLE_MODELS.values() else _get_model()

    if not keyword:
        return jsonify({"error": "키워드를 입력해주세요."}), 400

    # 카테고리별 프롬프트 및 플랫폼 제한
    if category == "travel":
        seo_prompts = SEO_PROMPTS_TRAVEL
        if platform == "blogspot":
            return jsonify({"error": "여행 카테고리는 블로그스팟을 지원하지 않습니다."}), 400
    elif category == "living":
        seo_prompts = SEO_PROMPTS_LIVING
        if platform != "naver":
            return jsonify({"error": "살림/생활 카테고리는 네이버 블로그만 지원합니다."}), 400
    elif category == "government":
        seo_prompts = SEO_PROMPTS_GOVERNMENT
        if platform == "blogspot":
            return jsonify({"error": "정부지원금 카테고리는 블로그스팟을 지원하지 않습니다."}), 400
    else:
        seo_prompts = SEO_PROMPTS_IT

    if platform not in seo_prompts:
        return jsonify({"error": "지원하지 않는 플랫폼입니다."}), 400

    tone_map = {
        "informative": "정보 전달형 (객관적이고 신뢰감 있는 톤)",
        "experience": "체험형 (1인칭 경험담, 솔직하고 생생한 톤)",
        "casual": "일상 대화형 (친근하고 편안한 톤)",
        "professional": "전문가형 (권위 있고 깊이 있는 톤)",
    }
    tone_desc = tone_map.get(tone, tone_map["informative"])

    # 네이버 + 정부지원금: 워킹맘 공감형 톤 자동 설정
    if platform == "naver" and category == "government":
        tone_desc = "워킹맘 공감형 (직접 찾아보고 신청해본 경험 기반)"

    system_prompt = seo_prompts[platform]

    # 현재 연도 안내 (AI가 과거 연도를 쓰지 않도록)
    system_prompt += f"\n\n현재 연도는 {datetime.now().year}년입니다. 제목과 본문에 연도가 필요하면 반드시 이 연도를 사용하세요."

    # 체험형 모드: 1인칭 경험담 톤 추가
    if tone == "experience":
        system_prompt += (
            "\n\n[체험형 글쓰기 모드]\n"
            "- 1인칭 경험담 형식으로 작성합니다\n"
            "- '제가 직접 해보니', '솔직히 말하면', '실제로 사용해보니', '직접 써본 결과' 등의 표현을 자연스럽게 활용합니다\n"
            "- 개인적인 감상과 솔직한 평가를 포함합니다\n"
            "- 독자에게 말하듯 친근한 톤을 유지합니다\n"
            "- '~했습니다' 보다는 '~했어요', '~더라고요' 체를 사용합니다\n"
            "- 구체적인 사용 상황과 맥락을 생생하게 묘사합니다"
        )

    if category == "living":
        tag_count = 20
        user_prompt = (
            f"다음 키워드로 네이버 블로그 '퇴근후 살림'에 올릴 생활 절약 글을 작성해주세요.\n\n"
            f"키워드: {keyword}\n"
            f"글 톤: {tone_desc}\n\n"
            "요구사항:\n"
            "1. 제목 후보 3가지를 제시하세요 (체험형 1개, 정보형 1개, 질문형 1개)\n"
            "   - 본문에는 체험형 제목을 사용하세요\n"
            "2. 본문 시작 전에 SEO 정보를 주석으로 포함하세요:\n"
            "   <!-- SEO: 추천카테고리 / 메인키워드 / 롱테일키워드 / 발행추천시간 -->\n"
            "3. 본문은 HTML 형식으로 작성하세요\n"
            "4. 이미지는 자동 삽입되므로 [이미지] 같은 플레이스홀더를 넣지 마세요\n"
            "5. HTML table 태그 사용 시 모든 셀에 반드시 구체적인 내용을 채우세요\n"
            "6. 광고 플레이스홀더 절대 금지\n"
            "7. 광고는 자동 삽입되므로 본문에 광고 관련 코드를 넣지 마세요\n"
            f"8. 태그는 반드시 {tag_count}개를 생성하세요 (인기 태그 10개 + 세부 틈새 태그 10개)\n"
            "9. 제목 후보 3가지는 태그 뒤에 별도 섹션으로 제공하세요\n\n"
            "응답 형식:\n"
            "---제목---\n(체험형 제목 텍스트)\n---본문---\n(HTML 본문)\n---태그---\n"
            f"(쉼표로 구분된 태그 {tag_count}개)\n"
            "---제목후보---\n(체험형: / 정보형: / 질문형: 각 한 줄씩)"
        )
    else:
        tag_count = 10
        # 워드프레스 subtype 안내
        subtype_desc = ""
        if platform == "wordpress" and subtype:
            subtype_map = {
                "review": "제품·서비스 리뷰 형식으로",
                "walkthrough": "단계별 공략·가이드 형식으로",
                "tips": "팁·추천·랭킹 형식으로",
                "government": "정부 지원금·정책 안내 형식으로",
            }
            subtype_desc = subtype_map.get(subtype, "")

        user_prompt = (
            f"다음 키워드로 {PLATFORM_NAMES[platform]}에 올릴 블로그 글을 "
            f"{subtype_desc + ' ' if subtype_desc else ''}작성해주세요.\n\n"
            f"키워드: {keyword}\n"
            f"글 톤: {tone_desc}\n\n"
            "요구사항:\n"
            "1. SEO에 최적화된 매력적인 제목을 작성하세요\n"
            "2. 본문은 HTML 형식으로 작성하세요\n"
            "3. 이미지는 자동 삽입되므로 [이미지] 같은 플레이스홀더를 넣지 마세요\n"
            "4. 독자의 관심을 끄는 도입부로 시작하세요\n"
            "5. 실용적인 정보와 팁을 포함하세요\n"
            "6. HTML table 태그 사용 시 모든 셀(td, th)에 반드시 구체적인 내용을 채우세요. 빈 셀은 절대 금지합니다\n"
            "7. <!-- 광고위치 --> 같은 광고 플레이스홀더 주석을 절대 넣지 마세요\n"
            "8. 'Advertisement' 텍스트가 들어간 더미 div를 절대 생성하지 마세요\n"
            "9. 광고는 자동 삽입되므로 본문에 광고 관련 코드를 넣지 마세요\n"
            f"10. 태그는 반드시 {tag_count}개를 생성하세요\n\n"
            "응답 형식:\n"
            f"---제목---\n(제목 텍스트)\n---본문---\n(HTML 본문)\n---태그---\n(쉼표로 구분된 태그 {tag_count}개)"
        )

    # ── 1단계: 제목·태그 생성 (Haiku – 빠르고 경제적) ──
    meta_prompt = (
        f"다음 키워드로 {PLATFORM_NAMES[platform]} 블로그 글의 제목과 태그만 생성해주세요.\n\n"
        f"키워드: {keyword}\n"
        f"글 톤: {tone_desc}\n"
        f"카테고리: {category}\n\n"
    )
    if category == "living":
        meta_prompt += (
            "요구사항:\n"
            "1. 제목 후보 3가지를 제시하세요 (체험형 1개, 정보형 1개, 질문형 1개)\n"
            "   - 제목에 이모티콘(ㅎㅎ, ㅠㅠ 등) 절대 금지\n"
            "2. 본문에 쓸 대표 제목은 체험형 제목으로 정하세요\n"
            f"3. 태그는 반드시 20개를 생성하세요 (인기 태그 10개 + 세부 틈새 태그 10개)\n\n"
            "응답 형식:\n"
            "---제목---\n(체험형 대표 제목)\n---태그---\n(쉼표로 구분된 태그 20개)\n"
            "---제목후보---\n(체험형: / 정보형: / 질문형: 각 한 줄씩)"
        )
    elif platform == "naver" and category == "government":
        meta_prompt += (
            "요구사항:\n"
            "1. 제목 규칙:\n"
            "   - 형식: 메인키워드 + 세부정보 (40자 이내)\n"
            "   - 실제 검색하는 형태 그대로 작성\n"
            "   - 금지: ~완벽정리, ~총정리, ~한눈에, 물음표 남발, 구어체 문장형 제목\n"
            "   - 좋은 예: '난방비 캐시백 2026 신청방법 자격조건', 'K-Gas 캐시백 신청 대상 금액 정리'\n"
            "   - 나쁜 예: '우리집도 받을 수 있다고? 직접 해봤는데 진짜 되더라구요'\n"
            "   - 제목에 이모티콘(ㅎㅎ, ㅠㅠ 등) 절대 금지\n"
            f"2. 태그는 반드시 {tag_count}개를 생성하세요\n\n"
            "응답 형식:\n"
            f"---제목---\n(제목 텍스트)\n---태그---\n(쉼표로 구분된 태그 {tag_count}개)"
        )
    else:
        meta_prompt += (
            "요구사항:\n"
            "1. SEO에 최적화된 매력적인 제목 1개를 작성하세요\n"
            f"2. 태그는 반드시 {tag_count}개를 생성하세요\n\n"
            "응답 형식:\n"
            f"---제목---\n(제목 텍스트)\n---태그---\n(쉼표로 구분된 태그 {tag_count}개)"
        )

    try:
        meta_resp = claude_client.messages.create(
            model=use_model,
            max_tokens=1000,
            system=system_prompt,
            messages=[{"role": "user", "content": meta_prompt}],
        )
        meta_content = meta_resp.content[0].text
    except anthropic.APIError as e:
        return jsonify({"error": f"제목/태그 생성 실패: {e.message}"}), 500

    # 제목·태그 파싱 (메타 응답에는 ---본문---이 없으므로 직접 파싱)
    title = ""
    tags = ""
    title_candidates = ""
    if "---제목---" in meta_content:
        after_title = meta_content.split("---제목---", 1)[1]
        if "---태그---" in after_title:
            title = after_title.split("---태그---", 1)[0].strip()
            tags_and_rest = after_title.split("---태그---", 1)[1].strip()
            if "---제목후보---" in tags_and_rest:
                tags, title_candidates = [p.strip() for p in tags_and_rest.split("---제목후보---", 1)]
            else:
                tags = tags_and_rest
        else:
            title = after_title.strip().split("\n")[0].strip()
    if not title:
        title = meta_content.strip().split("\n")[0].strip()

    # ── 2단계: 블로그 본문 생성 ──
    # 정부지원금/공략 등 최신 정보가 필요한 경우 웹 검색
    use_web_search = (category == "government" or subtype == "walkthrough")

    # 네이버 도입부 랜덤 선택
    naver_intros = [
        "이거 알고 계셨어요? 저는 최근에야 알았는데 꽤 쏠쏠하더라고요.",
        "주변에서 이거 신청했다는 얘기 듣고 저도 찾아봤어요.",
        "작년에 이거 몰라서 그냥 넘겼는데, 올해는 꼭 챙기려고요.",
        "아이 학교 엄마한테 듣고 바로 검색해봤어요.",
        "반신반의하면서 해봤는데 진짜 되더라고요.",
        "다른 방법이랑 비교해봤는데 이게 제일 간편했어요.",
        "이맘때쯤 꼭 챙겨야 하는 거라서 정리해봤어요.",
    ]
    selected_intro = random.choice(naver_intros)

    # 플랫폼별 body_prompt 분리
    if platform == "naver":
        body_prompt = (
            f"키워드: {keyword}\n"
            f"제목: {title}\n\n"
            f"도입부는 반드시 이 문장으로 시작해: '{selected_intro}'\n"
            "이후 위의 시스템 프롬프트에 정의된 말투, 도입부 패턴, 글 구조를 그대로 따라서 본문만 작성해줘.\n"
            "제목, 태그 포함하지 마. HTML 태그 사용 금지. ##H2:소제목## 형식 사용."
        )
    elif platform == "tistory":
        body_prompt = (
            f"키워드: {keyword}\n"
            f"제목: {title}\n\n"
            "위의 시스템 프롬프트에 정의된 말투, 도입부 패턴, 글 구조를 그대로 따라서 본문만 작성해줘.\n"
            "제목, 태그 포함하지 마. HTML 태그 사용 금지. ##H2:소제목## 형식 사용."
        )
    elif platform == "wordpress":
        body_prompt = (
            f"키워드: {keyword}\n"
            f"제목: {title}\n\n"
            "위의 시스템 프롬프트에 정의된 구조와 형식으로 본문만 작성해줘.\n"
            "HTML 형식으로 작성. 제목, 태그 포함하지 마."
        )
    else:
        body_prompt = (
            f"키워드: {keyword}\n"
            f"제목: {title}\n\n"
            "위의 시스템 프롬프트에 따라 본문만 작성해줘."
        )

    # 경쟁글 팩트가 있으면 프롬프트에 주입
    if competitor_facts:
        body_prompt += (
            f"\n\n[경쟁 블로그 분석 팩트 — 아래 정보를 본문에 자연스럽게 반영하세요]\n"
            f"{competitor_facts}\n\n"
            "위 팩트를 참고하되 그대로 복사하지 말고, 더 정확하고 풍부한 내용으로 재구성하세요."
        )

    try:
        if use_web_search:
            # 웹 검색(Haiku) → 본문 생성(Sonnet) 2단계
            body = _generate_body_with_web_search(system_prompt, body_prompt, keyword, category, use_model)
        else:
            body_resp = claude_client.messages.create(
                model=use_model,
                max_tokens=8000,
                system=system_prompt,
                messages=[{"role": "user", "content": body_prompt}],
            )
            body = body_resp.content[0].text.strip()
    except anthropic.APIError as e:
        return jsonify({"error": f"본문 생성 실패: {e.message}"}), 500

    # 메타설명 파싱 (티스토리)
    meta_description = ""
    if platform == "tistory" and "---메타설명---" in body:
        body, meta_desc_raw = body.split("---메타설명---", 1)
        meta_description = meta_desc_raw.strip()
        body = body.strip()

    # 본문 후처리
    # 0) 마크다운 코드펜스 제거 (```html ... ``` 등)
    body = re.sub(r'^```\w*\n?', '', body)
    body = re.sub(r'\n?```$', '', body)
    body = body.strip()
    # 1) 이미지/광고 플레이스홀더 및 JSON-LD 제거
    body = re.sub(r"\[이미지[^\]]*\]", "", body)
    body = re.sub(r"<!--\s*광고[^>]*-->", "", body)
    body = re.sub(r'<div[^>]*>\s*Advertisement\s*</div>', "", body, flags=re.IGNORECASE)
    body = re.sub(r'<script\s+type=["\']application/ld\+json["\']>.*?</script>', "", body, flags=re.DOTALL | re.IGNORECASE)
    # 2) Unsplash 대표 이미지 (featured_media 전용)
    thumbnail_url = ""
    all_images = _search_unsplash_images(keyword, 1, title=title)
    if all_images:
        thumbnail_url = all_images[0]["url"].split("?")[0] + "?w=800&h=800&fit=crop&fm=webp&q=80"
    # 4) 제목 주석을 HTML 최상단에 삽입
    body = f"<!-- 제목: {title} -->\n" + body
    # 5) 플랫폼별 AdSense 광고 삽입 (네이버는 애드센스 미지원)
    if platform != "naver":
        if platform == "tistory":
            ad_code = ADSENSE_TISTORY
            body = _insert_adsense_3(body, ad_code)
        else:
            ad_code = ADSENSE_IT
            body = _insert_adsense(body, ad_code)

    # 6) 정부지원금 카테고리: 면책 문구 자동 삽입
    if category == "government":
        body += "\n" + GOVERNMENT_DISCLAIMER

    result = {
        "title": title,
        "body": body,
        "tags": tags,
        "thumbnail": thumbnail_url,
        "platform": PLATFORM_NAMES[platform],
        "category": category,
    }
    if meta_description:
        result["meta_description"] = meta_description
    if title_candidates:
        result["title_candidates"] = title_candidates

    return jsonify(result)



def _generate_body_with_web_search(system_prompt: str, body_prompt: str, keyword: str, category: str = "", model: str = "") -> str:
    """웹 검색 → 요약 → 본문 생성의 2단계로 처리합니다.
    1단계: Haiku + web_search로 검색 결과 요약 (저비용)
    2단계: Sonnet으로 요약 기반 본문 작성 (검색 없이)"""
    use_model = model if model else _get_model()

    # ── 1단계: Haiku로 웹 검색 + 핵심 정보 요약 ──
    if category == "government":
        search_prompt = (
            f"다음 정부 지원금/정책에 대한 최신 정보를 웹 검색해서 핵심만 요약해주세요.\n\n"
            f"키워드: {keyword}\n\n"
            "요구사항:\n"
            "- 복지로(bokjiro.go.kr), 정부24(gov.kr) 등 공식 사이트 정보를 우선 검색하세요\n"
            "- 검색을 1~2회만 수행하세요\n"
            "- 다음 항목을 반드시 포함: 지원 대상, 지원 금액, 신청 기간, 신청 방법\n"
            "- 구체적 수치(금액, 날짜)를 반드시 포함하세요\n"
            "- 총 500자 이내로 요약하세요\n"
            "- 출처 URL은 생략하세요"
        )
    else:
        search_prompt = (
            f"다음 키워드에 대한 최신 공략/가이드 정보를 웹 검색해서 핵심만 요약해주세요.\n\n"
            f"키워드: {keyword}\n\n"
            "요구사항:\n"
            "- 검색을 1~2회만 수행하세요\n"
            "- 검색 결과에서 핵심 정보만 불릿 포인트로 정리하세요\n"
            "- 수치, 단계, 팁 등 구체적 정보 위주로 요약하세요\n"
            "- 총 500자 이내로 요약하세요\n"
            "- 출처 URL은 생략하세요"
        )
    tools = [{"type": "web_search_20250305", "name": "web_search"}]

    search_summary = ""
    messages = [{"role": "user", "content": search_prompt}]

    for _ in range(3):  # 최대 3회 continuation
        try:
            resp = claude_client.messages.create(
                model=use_model,
                max_tokens=1024,
                messages=messages,
                tools=tools,
            )
        except anthropic.APIError:
            break

        for block in resp.content:
            if block.type == "text":
                search_summary += block.text

        if resp.stop_reason == "end_turn":
            break

        if resp.stop_reason == "pause_turn":
            messages = [
                {"role": "user", "content": search_prompt},
                {"role": "assistant", "content": resp.content},
            ]
            search_summary = ""
            continue

        break

    search_summary = search_summary.strip()

    # ── 2단계: Sonnet으로 요약 기반 본문 생성 (검색 없이) ──
    if search_summary:
        enriched_prompt = (
            f"{body_prompt}\n\n"
            f"[웹 검색으로 수집한 최신 정보]\n{search_summary}\n\n"
            "위 검색 정보를 참고하여 정확하고 최신의 내용으로 본문을 작성하세요."
        )
    else:
        enriched_prompt = body_prompt

    body_resp = claude_client.messages.create(
        model=use_model,
        max_tokens=8000,
        system=system_prompt,
        messages=[{"role": "user", "content": enriched_prompt}],
    )

    result = body_resp.content[0].text.strip()
    result = re.sub(r'^```\w*\n?', '', result)
    result = re.sub(r'\n?```$', '', result)
    return result.strip()


def _generate_imagen_thumbnail(title: str, keyword: str) -> Optional[bytes]:
    """Google Imagen 3로 썸네일 생성 → 800x800 크롭 + 제목 오버레이 → WebP bytes 반환."""
    if not GOOGLE_API_KEY:
        print("[Imagen] GOOGLE_API_KEY 미설정")
        return None

    try:
        from google import genai
        from PIL import Image, ImageDraw, ImageFont
        import io
        import textwrap

        # 1) 제목을 영어로 번역
        en_resp = claude_client.messages.create(
            model=_get_model(),
            max_tokens=80,
            messages=[{"role": "user", "content":
                f"Translate this Korean title to concise English (one line, no quotes):\n{title}"}],
        )
        en_title = en_resp.content[0].text.strip().strip('"\'')
        print(f"[Imagen] 제목 번역: {title!r} → {en_title!r}")

        # 2) Imagen 3로 이미지 생성
        client = genai.Client(api_key=GOOGLE_API_KEY)
        prompt = (
            f"A clean, professional blog thumbnail about {en_title}, "
            "bright colors, no text, photorealistic"
        )
        response = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=prompt,
            config=genai.types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1",
            ),
        )

        if not response.generated_images:
            print("[Imagen] 이미지 생성 실패: 결과 없음")
            return None

        img_bytes = response.generated_images[0].image.image_bytes
        img = Image.open(io.BytesIO(img_bytes))

        # 3) 800x800 크롭
        img = img.resize((800, 800), Image.LANCZOS)

        # 4) 제목 텍스트 오버레이 (중앙 상단)
        draw = ImageDraw.Draw(img)
        font_path = os.path.join(_APP_DIR, "fonts", "NanumGothicBold.ttf")
        try:
            font = ImageFont.truetype(font_path, 38)
        except OSError:
            font = ImageFont.load_default()
            print(f"[Imagen] 폰트 로드 실패: {font_path}, 기본 폰트 사용")

        # 텍스트 줄바꿈 (최대 16자씩)
        lines = textwrap.wrap(title, width=16)
        if len(lines) > 3:
            lines = lines[:3]
            lines[-1] = lines[-1][:14] + "…"

        # 배경 박스 크기 계산
        line_bboxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        line_heights = [bb[3] - bb[1] for bb in line_bboxes]
        line_widths = [bb[2] - bb[0] for bb in line_bboxes]
        total_h = sum(line_heights) + (len(lines) - 1) * 8
        max_w = max(line_widths) if line_widths else 0

        pad_x, pad_y = 32, 20
        box_x = (800 - max_w) // 2 - pad_x
        box_y = 40
        box_w = max_w + pad_x * 2
        box_h = total_h + pad_y * 2

        # 반투명 검정 배경
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rounded_rectangle(
            [box_x, box_y, box_x + box_w, box_y + box_h],
            radius=12, fill=(0, 0, 0, 140),
        )
        img = Image.alpha_composite(img.convert("RGBA"), overlay)
        draw = ImageDraw.Draw(img)

        # 텍스트 렌더링
        y_cursor = box_y + pad_y
        for i, line in enumerate(lines):
            lw = line_widths[i]
            x = (800 - lw) // 2
            draw.text((x, y_cursor), line, fill="white", font=font)
            y_cursor += line_heights[i] + 8

        # 5) WebP 변환
        out_buf = io.BytesIO()
        img.convert("RGB").save(out_buf, format="WEBP", quality=85)
        print(f"[Imagen] 썸네일 생성 완료: {len(out_buf.getvalue())} bytes")
        return out_buf.getvalue()

    except Exception as e:
        print(f"[Imagen] 썸네일 생성 실패: {e}")
        import traceback; traceback.print_exc()
        return None


def _translate_keyword_for_unsplash(keyword: str, title: str = "") -> str:
    """글 제목 + 키워드에서 핵심 의미를 추출하여 Unsplash 검색용 영어 쿼리로 변환."""
    source = title if title else keyword
    if not re.search(r"[가-힣]", source):
        return source

    try:
        resp = claude_client.messages.create(
            model=_get_model(),
            max_tokens=60,
            messages=[{"role": "user", "content":
                f"아래 한국어 블로그 제목에서 핵심 주제를 파악하고, "
                f"Unsplash에서 관련 사진을 찾을 수 있는 영어 검색어 2~4단어로 변환해주세요.\n"
                f"구체적인 장소나 사물 위주로, 추상적 단어(support, program, guide) 대신 "
                f"시각적으로 촬영 가능한 대상을 선택하세요.\n"
                f"따옴표나 설명 없이 검색어만 출력하세요.\n\n"
                f"제목: {source}\n키워드: {keyword}"}],
        )
        translated = resp.content[0].text.strip().strip('"\'')
        print(f"[Unsplash] 검색어 변환: {source!r} → {translated!r}")
        return translated
    except Exception as e:
        print(f"[Unsplash] 번역 실패: {e}")
    return keyword


def _search_unsplash_images(keyword: str, count: int, used_ids=None, title: str = "") -> list:
    """Unsplash에서 키워드 관련 이미지를 검색하여 URL을 반환합니다.
    글 제목 + 키워드를 분석하여 영어 검색어로 변환 후 검색합니다.
    검색 결과가 부족하면 키워드를 단순화하여 재시도합니다."""
    if not UNSPLASH_ACCESS_KEY or UNSPLASH_ACCESS_KEY == "여기에Access키붙여넣기":
        return []
    if count <= 0:
        return []
    if used_ids is None:
        used_ids = set()

    query = _translate_keyword_for_unsplash(keyword, title=title)

    # 검색어 후보: 원본 → 첫 단어 → 일반 fallback
    queries_to_try = [query]
    first_word = query.split()[0] if query.split() else query
    if first_word != query:
        queries_to_try.append(first_word)
    queries_to_try.append("technology blog")  # 최종 fallback

    collected: list[dict] = []

    for q in queries_to_try:
        if len(collected) >= count:
            break
        page = 1
        per_page = max(min(count + 5, 20), 10)
        for _ in range(2):  # 최대 2페이지
            try:
                resp = http_requests.get(
                    "https://api.unsplash.com/search/photos",
                    params={
                        "query": q,
                        "orientation": "landscape",
                        "per_page": per_page,
                        "page": page,
                    },
                    headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    break
                results = resp.json().get("results", [])
                if not results:
                    break
                for r in results:
                    photo_id = r["id"]
                    if photo_id in used_ids:
                        continue
                    used_ids.add(photo_id)
                    collected.append({
                        "id": photo_id,
                        "url": r["urls"]["raw"] + "?w=800&fm=webp&q=80",
                        "alt": r.get("alt_description") or keyword,
                        "credit": r["user"]["name"],
                        "link": r["user"]["links"]["html"],
                    })
                    if len(collected) >= count:
                        return collected
                page += 1
            except Exception:
                break

    return collected


def _insert_images_at_h2(body: str, keyword: str, images=None) -> str:
    """h2 섹션에 Unsplash 이미지 삽입. 최대 3장, 균등 분배."""
    h2_pattern = re.compile(r"(<h2[^>]*>)(.*?)(</h2>)", re.IGNORECASE | re.DOTALL)
    h2_matches = list(h2_pattern.finditer(body))
    if not h2_matches:
        return body

    if not images:
        images = _search_unsplash_images(keyword, min(3, len(h2_matches)))
    if not images:
        return body

    # 최대 3장으로 제한
    images = images[:3]

    # h2가 많으면 균등 분배 (예: h2 5개, 이미지 3개 → 0, 2, 4번째 h2에 삽입)
    n_h2 = len(h2_matches)
    n_img = len(images)
    if n_h2 <= n_img:
        target_indices = list(range(n_h2))[:n_img]
    else:
        # 균등 분배: 첫 번째, 중간, 마지막 근처
        step = max(1, n_h2 / n_img)
        target_indices = [min(int(i * step), n_h2 - 1) for i in range(n_img)]
        # 중복 제거
        target_indices = list(dict.fromkeys(target_indices))

    # 뒤에서부터 삽입해야 인덱스가 밀리지 않음
    for img_idx, h2_idx in reversed(list(enumerate(target_indices))):
        if img_idx >= len(images):
            continue
        match = h2_matches[h2_idx]
        img = images[img_idx]
        h2_text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        alt_text = f"{h2_text} 관련 이미지" if h2_text else f"{keyword} 관련 이미지"
        img_html = (
            f'\n<figure style="margin:1.2em 0;text-align:center;">'
            f'<img src="{img["url"]}" alt="{alt_text}" '
            f'style="max-width:100%;height:auto;border-radius:8px;" loading="lazy">'
            f'<figcaption style="font-size:0.8em;color:#888;margin-top:0.4em;">'
            f'Photo by <a href="{img["link"]}?utm_source=blog_tool&utm_medium=referral" '
            f'target="_blank" rel="noopener">{img["credit"]}</a> on Unsplash'
            f'</figcaption></figure>\n'
        )
        pos = match.end()
        body = body[:pos] + img_html + body[pos:]

    return body


def _insert_adsense(body: str, ad_code: str = "") -> str:
    """본문에 AdSense 광고 코드 삽입. </p> 뒤에만, 이미지 근처 금지."""
    if not ad_code:
        ad_code = ADSENSE_CODE
    if not ad_code:
        return body

    ad_block = (
        f'\n<div class="ad-container" style="margin:1.5em 0;text-align:center;">'
        f"{ad_code}"
        f"</div>\n"
    )

    # </p> 위치만 수집 (텍스트 단락 뒤에만 광고 삽입 가능)
    safe_positions = _find_safe_ad_positions(body)
    if not safe_positions:
        return body

    # 4곳 균등 분배
    n = len(safe_positions)
    if n >= 4:
        indices = [0, n // 4, n // 2, n * 3 // 4]
    elif n >= 3:
        indices = [0, n // 2, n - 1]
    elif n >= 2:
        indices = [0, n - 1]
    else:
        indices = [0]

    positions = sorted(set(safe_positions[i] for i in indices), reverse=True)
    for pos in positions:
        body = body[:pos] + ad_block + body[pos:]

    return body


def _find_safe_ad_positions(body: str) -> list[int]:
    """</p> 뒤 중 앞뒤로 이미지(<figure>, <img>)가 없는 안전한 위치만 반환."""
    p_close_pattern = re.compile(r"</p>", re.IGNORECASE)
    img_pattern = re.compile(r"<(?:figure|img)\b", re.IGNORECASE)
    img_close_pattern = re.compile(r"</figure>", re.IGNORECASE)

    # 이미지/figure 태그의 시작·끝 위치 수집
    img_zones = []
    for m in re.finditer(r"<figure[^>]*>.*?</figure>", body, re.IGNORECASE | re.DOTALL):
        img_zones.append((m.start(), m.end()))
    for m in re.finditer(r"<img\b[^>]*>", body, re.IGNORECASE):
        img_zones.append((m.start() - 10, m.end() + 10))  # 약간의 여유

    safe = []
    for m in p_close_pattern.finditer(body):
        pos = m.end()
        # 이 위치 앞뒤 200자 이내에 이미지가 있는지 확인
        near_image = False
        for img_start, img_end in img_zones:
            if abs(pos - img_start) < 200 or abs(pos - img_end) < 200:
                near_image = True
                break
        if not near_image:
            safe.append(pos)
    return safe


def _insert_adsense_3(body: str, ad_code: str) -> str:
    """티스토리용: 이미지 근처를 피해 </p> 뒤 안전한 위치 3곳에 삽입."""
    if not ad_code:
        return body

    ad_block = (
        f'\n<div class="ad-container" style="margin:1.5em 0;text-align:center;">'
        f"{ad_code}"
        f"</div>\n"
    )

    safe = _find_safe_ad_positions(body)
    if not safe:
        return body

    # 3곳 균등 분배
    n = len(safe)
    if n >= 3:
        indices = [0, n // 2, n - 1]
    elif n == 2:
        indices = [0, 1]
    else:
        indices = [0]

    positions = sorted(set(safe[i] for i in indices), reverse=True)
    for pos in positions:
        body = body[:pos] + ad_block + body[pos:]

    return body


def _parse_article(content: str) -> tuple[str, str, str, str]:
    title = ""
    body = ""
    tags = ""
    title_candidates = ""

    if "---제목---" in content and "---본문---" in content:
        parts = content.split("---본문---")
        title_part = parts[0].split("---제목---")[-1].strip()
        title = title_part.strip()

        body_and_tags = parts[1] if len(parts) > 1 else ""
        if "---태그---" in body_and_tags:
            body_parts = body_and_tags.split("---태그---")
            body = body_parts[0].strip()
            tags_and_rest = body_parts[1].strip() if len(body_parts) > 1 else ""
            # 제목후보 섹션 분리
            if "---제목후보---" in tags_and_rest:
                tags_parts = tags_and_rest.split("---제목후보---")
                tags = tags_parts[0].strip()
                title_candidates = tags_parts[1].strip() if len(tags_parts) > 1 else ""
            else:
                tags = tags_and_rest
        else:
            body = body_and_tags.strip()
    else:
        lines = content.strip().split("\n")
        title = lines[0] if lines else "제목 없음"
        body = "\n".join(lines[1:])

    return title, body, tags, title_candidates


# ──────────────────────────────────────────────
# 1단계: WordPress REST API 자동 발행
# ──────────────────────────────────────────────

def _wp_auth_header() -> dict:
    """WordPress Application Password Basic Auth 헤더."""
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _wp_upload_image(image_url: str, filename: str):
    """이미지 URL을 다운로드하여 WP 미디어에 업로드하고 media ID를 반환."""
    try:
        img_resp = http_requests.get(image_url, timeout=15)
        if img_resp.status_code != 200:
            return None

        content_type = img_resp.headers.get("Content-Type", "image/jpeg")
        headers = _wp_auth_header()
        headers["Content-Type"] = content_type
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

        resp = http_requests.post(
            f"{WP_URL}/wp-json/wp/v2/media",
            headers=headers,
            data=img_resp.content,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id")
    except Exception as e:
        print(f"WP 이미지 업로드 실패: {e}")
    return None


def _wp_get_or_create_category(name: str):
    """카테고리를 이름으로 검색하고, 없으면 생성."""
    try:
        resp = http_requests.get(
            f"{WP_URL}/wp-json/wp/v2/categories",
            params={"search": name, "per_page": 1},
            headers=_wp_auth_header(),
            timeout=10,
        )
        if resp.status_code == 200 and resp.json():
            return resp.json()[0]["id"]

        resp = http_requests.post(
            f"{WP_URL}/wp-json/wp/v2/categories",
            headers={**_wp_auth_header(), "Content-Type": "application/json"},
            json={"name": name},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id")
    except Exception as e:
        print(f"WP 카테고리 처리 실패: {e}")
    return None


def _wp_get_or_create_tags(tag_names: list[str]) -> list[int]:
    """태그 이름 목록을 WP 태그 ID로 변환 (없으면 생성)."""
    tag_ids = []
    for name in tag_names[:15]:
        name = name.strip()
        if not name:
            continue
        try:
            resp = http_requests.get(
                f"{WP_URL}/wp-json/wp/v2/tags",
                params={"search": name, "per_page": 1},
                headers=_wp_auth_header(),
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                tag_ids.append(resp.json()[0]["id"])
                continue

            resp = http_requests.post(
                f"{WP_URL}/wp-json/wp/v2/tags",
                headers={**_wp_auth_header(), "Content-Type": "application/json"},
                json={"name": name},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                tag_ids.append(resp.json().get("id"))
        except Exception as e:
            print(f"WP 태그 처리 실패 ({name}): {e}")
    return tag_ids


# 카테고리 매핑
WP_CATEGORY_MAP = {
    "it": "IT",
    "travel": "여행",
    "living": "생활",
    "review": "리뷰",
    "walkthrough": "공략",
    "tips": "팁",
    "government": "정부지원금",
}


@app.route("/publish-wordpress", methods=["POST"])
def publish_wordpress():
    """WordPress REST API로 글 발행."""
    if not WP_URL or not WP_APP_PASSWORD:
        return jsonify({"error": "WordPress 설정이 없습니다. .env에 WP_URL, WP_USER, WP_APP_PASSWORD를 설정해주세요."}), 400

    data = request.get_json()
    title = data.get("title", "")
    body = data.get("body", "")
    tags_str = data.get("tags", "")
    category = data.get("category", "it")
    subtype = data.get("subtype", "")
    thumbnail_url = data.get("thumbnail", "")
    focus_keyword = data.get("focus_keyword", "")

    if not title or not body:
        return jsonify({"error": "제목과 본문이 필요합니다."}), 400

    # 포커스 키워드 fallback: 첫 번째 태그 사용
    if not focus_keyword and tags_str:
        focus_keyword = tags_str.split(",")[0].strip()

    steps = []

    # 1) 대표 이미지 업로드
    featured_media_id = None
    if thumbnail_url:
        safe_title = re.sub(r'[^\w가-힣]', '-', title)[:50]
        featured_media_id = _wp_upload_image(thumbnail_url, f"{safe_title}.webp")
        if featured_media_id:
            steps.append({"step": "image_upload", "status": "success", "media_id": featured_media_id})
        else:
            steps.append({"step": "image_upload", "status": "failed"})

    # 2) 카테고리 매핑
    cat_name = WP_CATEGORY_MAP.get(subtype) or WP_CATEGORY_MAP.get(category, "IT")
    cat_id = _wp_get_or_create_category(cat_name)
    categories = [cat_id] if cat_id else []

    # 3) 태그 매핑
    tag_names = [t.strip() for t in tags_str.split(",") if t.strip()]
    tag_ids = _wp_get_or_create_tags(tag_names)

    # 4) Rank Math SEO 메타 + 슬러그 준비
    slug = re.sub(r'[^\w가-힣\s-]', '', focus_keyword).strip().replace(' ', '-')[:80] if focus_keyword else ""
    meta_description = re.sub(r'<[^>]+>', '', body)[:155].strip() if body else ""

    # 5) 글 발행
    post_data = {
        "title": title,
        "content": body,
        "status": "publish",
        "categories": categories,
        "tags": tag_ids,
        "meta": {
            "rank_math_focus_keyword": focus_keyword,
            "rank_math_title": f"{title} - %sitename%",
            "rank_math_description": meta_description,
        },
    }
    if slug:
        post_data["slug"] = slug
    if featured_media_id:
        post_data["featured_media"] = featured_media_id

    try:
        resp = http_requests.post(
            f"{WP_URL}/wp-json/wp/v2/posts",
            headers={**_wp_auth_header(), "Content-Type": "application/json"},
            json=post_data,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            post = resp.json()
            post_url = post.get("link", "")
            post_id = post.get("id")
            steps.append({"step": "publish", "status": "success", "post_id": post_id, "url": post_url})
            return jsonify({"success": True, "post_url": post_url, "post_id": post_id, "steps": steps})
        else:
            error_msg = resp.json().get("message", resp.text[:200])
            steps.append({"step": "publish", "status": "failed", "error": error_msg})
            return jsonify({"error": f"발행 실패: {error_msg}", "steps": steps}), 500
    except Exception as e:
        steps.append({"step": "publish", "status": "failed", "error": str(e)})
        return jsonify({"error": f"발행 실패: {str(e)}", "steps": steps}), 500


# ──────────────────────────────────────────────
# 네이버 블로그 자동 발행 (Playwright)
# ──────────────────────────────────────────────

@app.route("/publish-naver", methods=["POST"])
def publish_naver():
    """네이버 블로그 Playwright 자동 발행."""
    if not NAVER_BLOG_ID:
        return jsonify({"error": "NAVER_BLOG_ID가 설정되지 않았습니다. .env에 추가해주세요."}), 400

    import naver_playwright

    if not naver_playwright.cookies_exist():
        return jsonify({"error": "네이버 쿠키가 없습니다. /naver-login으로 먼저 로그인해주세요."}), 400

    data = request.get_json()
    title = data.get("title", "")
    body = data.get("body", "")
    tags_str = data.get("tags", "")

    if not title or not body:
        return jsonify({"error": "제목과 본문이 필요합니다."}), 400

    tag_list = [t.strip() for t in tags_str.split(",") if t.strip()][:10]

    result = naver_playwright.publish_to_naver(title, body, tag_list)

    if result.get("success"):
        return jsonify(result)
    else:
        return jsonify(result), 500


@app.route("/naver-login", methods=["GET", "POST"])
def naver_login_page():
    """네이버 로그인 (쿠키 저장).

    GET: 수동 로그인용 브라우저 실행 (headless=False, 로컬 전용)
    POST: 쿠키 JSON 직접 업로드 (headless 서버용)
    """
    import naver_playwright

    if request.method == "POST":
        # 쿠키 JSON 업로드 방식
        data = request.get_json()
        cookies_json = data.get("cookies", "")
        if not cookies_json:
            return jsonify({"error": "cookies 필드가 필요합니다."}), 400

        if isinstance(cookies_json, list):
            cookies_json = json.dumps(cookies_json)

        result = naver_playwright.upload_cookies(cookies_json)
        if result["success"]:
            return jsonify({"success": True, "message": f"쿠키 {result['cookie_count']}개 저장 완료"})
        else:
            return jsonify({"error": result["error"]}), 400

    # GET: 브라우저 실행 (로컬 전용)
    result = naver_playwright.login_and_save_cookies()
    if result["success"]:
        return jsonify({"success": True, "message": f"로그인 성공, 쿠키 {result['cookie_count']}개 저장"})
    else:
        return jsonify({"error": result["error"]}), 400


@app.route("/naver-cookie-status")
def naver_cookie_status():
    """네이버 쿠키 상태 확인."""
    import naver_playwright
    exists = naver_playwright.cookies_exist()
    return jsonify({"exists": exists, "blog_id": NAVER_BLOG_ID or "(미설정)"})


# ──────────────────────────────────────────────
# 티스토리 블로그 자동 발행 (Playwright)
# ──────────────────────────────────────────────

@app.route("/publish-tistory", methods=["POST"])
def publish_tistory():
    """티스토리 블로그 Playwright 자동 발행."""
    import tistory_playwright

    data = request.get_json()
    blog_id = data.get("blog_id", "")
    title = data.get("title", "")
    body = data.get("body", "")
    tags_str = data.get("tags", "")

    if not blog_id:
        return jsonify({"error": "blog_id가 필요합니다."}), 400
    if blog_id not in TISTORY_BLOGS:
        return jsonify({"error": f"허용되지 않은 블로그: {blog_id}"}), 400
    if not title or not body:
        return jsonify({"error": "제목과 본문이 필요합니다."}), 400

    if not tistory_playwright.cookies_exist(blog_id):
        return jsonify({"error": f"{blog_id} 쿠키가 없습니다. 먼저 로그인해주세요."}), 400

    tag_list = [t.strip() for t in tags_str.split(",") if t.strip()][:10]

    result = tistory_playwright.publish_to_tistory(blog_id, title, body, tag_list)

    if result.get("success"):
        return jsonify(result)
    else:
        return jsonify(result), 500


@app.route("/tistory-cookie-status")
def tistory_cookie_status():
    """티스토리 블로그별 쿠키 상태 확인."""
    import tistory_playwright
    blogs = {}
    for blog_id in TISTORY_BLOGS:
        blogs[blog_id] = {
            "exists": tistory_playwright.cookies_exist(blog_id),
            "url": f"https://{blog_id}.tistory.com",
        }
    return jsonify({"blogs": blogs})


@app.route("/tistory-login", methods=["POST"])
def tistory_login():
    """티스토리 쿠키 업로드."""
    import tistory_playwright
    data = request.get_json()
    blog_id = data.get("blog_id", "")
    cookies_json = data.get("cookies", "")
    if not blog_id or not cookies_json:
        return jsonify({"error": "blog_id와 cookies가 필요합니다."}), 400
    result = tistory_playwright.upload_cookies(blog_id, cookies_json)
    if result["success"]:
        return jsonify(result)
    else:
        return jsonify({"error": result["error"]}), 400


# ──────────────────────────────────────────────
# 2단계: IndexNow (Bing / Naver)
# ──────────────────────────────────────────────

def _submit_indexnow(url: str) -> list[dict]:
    """IndexNow API로 Bing과 Naver에 URL을 제출."""
    results = []
    endpoints = [
        ("Bing", "https://api.indexnow.org/indexnow"),
        ("Naver", "https://searchadvisor.naver.com/indexnow"),
    ]

    payload = {
        "host": WP_URL.replace("https://", "").replace("http://", ""),
        "key": INDEXNOW_KEY,
        "urlList": [url],
    }

    for name, endpoint in endpoints:
        try:
            resp = http_requests.post(
                endpoint,
                headers={"Content-Type": "application/json; charset=utf-8"},
                json=payload,
                timeout=15,
            )
            results.append({
                "service": name,
                "status": "success" if resp.status_code in (200, 202) else "failed",
                "http_status": resp.status_code,
            })
        except Exception as e:
            results.append({"service": name, "status": "failed", "error": str(e)})

    return results


@app.route("/indexnow-key")
def indexnow_key_file():
    """IndexNow 키 검증 파일 엔드포인트."""
    return INDEXNOW_KEY, 200, {"Content-Type": "text/plain"}


# ──────────────────────────────────────────────
# 3단계: Google Indexing API
# ──────────────────────────────────────────────

def _submit_google_indexing(url: str) -> dict:
    """Google Indexing API로 URL 색인 요청."""
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GoogleAuthRequest

        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_PATH,
            scopes=["https://www.googleapis.com/auth/indexing"],
        )
        credentials.refresh(GoogleAuthRequest())

        resp = http_requests.post(
            "https://indexing.googleapis.com/v3/urlNotifications:publish",
            headers={
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json",
            },
            json={"url": url, "type": "URL_UPDATED"},
            timeout=15,
        )

        if resp.status_code == 200:
            return {"service": "Google", "status": "success"}
        else:
            error_msg = resp.json().get("error", {}).get("message", resp.text[:200])
            return {"service": "Google", "status": "failed", "error": error_msg, "http_status": resp.status_code}
    except FileNotFoundError:
        return {"service": "Google", "status": "skipped", "error": "google-credentials.json 파일이 없습니다"}
    except ImportError:
        return {"service": "Google", "status": "skipped", "error": "google-auth 라이브러리가 설치되지 않았습니다"}
    except Exception as e:
        return {"service": "Google", "status": "failed", "error": str(e)}


# ──────────────────────────────────────────────
# 통합 라우트: 발행 → IndexNow → Google Indexing
# ──────────────────────────────────────────────

@app.route("/publish-and-index", methods=["POST"])
def publish_and_index():
    """WordPress 발행 + IndexNow + Google Indexing 통합 실행."""
    if not WP_URL or not WP_APP_PASSWORD:
        return jsonify({"error": "WordPress 설정이 없습니다."}), 400

    data = request.get_json()
    pipeline_results = {"steps": []}

    # Step 1: WordPress 발행
    title = data.get("title", "")
    body = data.get("body", "")
    tags_str = data.get("tags", "")
    category = data.get("category", "it")
    subtype = data.get("subtype", "")
    thumbnail_url = data.get("thumbnail", "")
    focus_keyword = data.get("focus_keyword", "")

    if not title or not body:
        return jsonify({"error": "제목과 본문이 필요합니다."}), 400

    # 포커스 키워드 fallback: 첫 번째 태그 사용
    if not focus_keyword and tags_str:
        focus_keyword = tags_str.split(",")[0].strip()

    # 이미지 업로드
    featured_media_id = None
    if thumbnail_url:
        safe_title = re.sub(r'[^\w가-힣]', '-', title)[:50]
        featured_media_id = _wp_upload_image(thumbnail_url, f"{safe_title}.webp")

    # 카테고리/태그
    cat_name = WP_CATEGORY_MAP.get(subtype) or WP_CATEGORY_MAP.get(category, "IT")
    cat_id = _wp_get_or_create_category(cat_name)
    categories = [cat_id] if cat_id else []
    tag_names = [t.strip() for t in tags_str.split(",") if t.strip()]
    tag_ids = _wp_get_or_create_tags(tag_names)

    # Rank Math SEO 메타 + 슬러그
    slug = re.sub(r'[^\w가-힣\s-]', '', focus_keyword).strip().replace(' ', '-')[:80] if focus_keyword else ""
    meta_description = re.sub(r'<[^>]+>', '', body)[:155].strip() if body else ""

    post_data = {
        "title": title,
        "content": body,
        "status": "publish",
        "categories": categories,
        "tags": tag_ids,
        "meta": {
            "rank_math_focus_keyword": focus_keyword,
            "rank_math_title": f"{title} - %sitename%",
            "rank_math_description": meta_description,
        },
        **({"slug": slug} if slug else {}),
        **({"featured_media": featured_media_id} if featured_media_id else {}),
    }

    post_url = ""
    try:
        resp = http_requests.post(
            f"{WP_URL}/wp-json/wp/v2/posts",
            headers={**_wp_auth_header(), "Content-Type": "application/json"},
            json=post_data,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            post = resp.json()
            post_url = post.get("link", "")
            pipeline_results["steps"].append({
                "step": "WordPress 발행",
                "status": "success",
                "url": post_url,
                "post_id": post.get("id"),
            })
        else:
            error_msg = resp.json().get("message", resp.text[:200])
            pipeline_results["steps"].append({"step": "WordPress 발행", "status": "failed", "error": error_msg})
            return jsonify({"error": f"발행 실패: {error_msg}", **pipeline_results}), 500
    except Exception as e:
        pipeline_results["steps"].append({"step": "WordPress 발행", "status": "failed", "error": str(e)})
        return jsonify({"error": f"발행 실패: {str(e)}", **pipeline_results}), 500

    # Step 2: IndexNow (Bing + Naver)
    if post_url:
        indexnow_results = _submit_indexnow(post_url)
        for r in indexnow_results:
            pipeline_results["steps"].append({
                "step": f"IndexNow ({r['service']})",
                "status": r["status"],
                **({k: v for k, v in r.items() if k not in ("service", "status")}),
            })

    # Step 3: Google Indexing API
    if post_url:
        google_result = _submit_google_indexing(post_url)
        pipeline_results["steps"].append({
            "step": "Google Indexing",
            "status": google_result["status"],
            **({k: v for k, v in google_result.items() if k not in ("service", "status")}),
        })

    pipeline_results["success"] = True
    pipeline_results["post_url"] = post_url
    return jsonify(pipeline_results)


# ──────────────────────────────────────────────
# 대시보드 & 키워드 큐 API
# ──────────────────────────────────────────────

import uuid as _uuid
from datetime import timezone as _tz

_DATA_DIR = os.path.join(_APP_DIR, "data")
_QUEUE_PATH = os.path.join(_DATA_DIR, "keyword_queue.json")
_CONFIG_PATH = os.path.join(_DATA_DIR, "scheduler_config.json")
_LOG_PATH = os.path.join(_DATA_DIR, "publish_log.json")


def _load_json(path, default=None):
    """JSON 파일 로드 (없으면 기본값 반환)."""
    if default is None:
        default = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path, data):
    """JSON 파일 저장."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


_ACCOUNTS_PATH = os.path.join(_DATA_DIR, "accounts.json")


def _load_accounts():
    """계정 목록 로드. 없으면 .env 기반 기본값 생성."""
    accounts = _load_json(_ACCOUNTS_PATH, None)
    if accounts is None:
        # 초기 accounts.json 생성 (.env 기반)
        accounts = {"naver": [], "tistory": [], "wordpress": []}
        if NAVER_BLOG_ID:
            accounts["naver"].append({
                "id": NAVER_BLOG_ID,
                "name": NAVER_BLOG_ID,
                "url": f"https://blog.naver.com/{NAVER_BLOG_ID}",
                "blog_id": NAVER_BLOG_ID,
            })
        for blog_id in TISTORY_BLOGS:
            accounts["tistory"].append({
                "id": blog_id,
                "name": blog_id,
                "url": f"https://{blog_id}.tistory.com",
                "blog_id": blog_id,
            })
        if WP_URL:
            accounts["wordpress"].append({
                "id": "wp-main",
                "name": WP_URL.replace("https://", "").replace("http://", ""),
                "url": WP_URL,
                "blog_id": "wp-main",
            })
        _save_json(_ACCOUNTS_PATH, accounts)
    return accounts


def _default_config():
    return {
        "enabled": False,
        "min_interval_min": 30,
        "max_interval_min": 120,
        "start_hour": 7,
        "end_hour": 23,
        "last_run_at": None,
        "next_run_at": None,
    }


@app.route("/api/dashboard/status")
def api_dashboard_status():
    """대시보드 상태 정보."""
    import naver_playwright
    import tistory_playwright as tistory_pw

    queue = _load_json(_QUEUE_PATH, [])
    config = _load_json(_CONFIG_PATH, _default_config())
    logs = _load_json(_LOG_PATH, [])

    today = datetime.now().strftime("%Y-%m-%d")
    today_logs = [l for l in logs if l.get("timestamp", "").startswith(today)]

    # Current model info
    current_model_id = _get_model()
    model_key = next((k for k, v in AVAILABLE_MODELS.items() if v == current_model_id), "haiku")

    return jsonify({
        "scheduler": config,
        "counts": {
            "today_published": sum(1 for l in today_logs if l.get("status") == "success"),
            "pending": sum(1 for q in queue if q.get("status") == "pending"),
            "failed": sum(1 for q in queue if q.get("status") == "failed"),
            "processing": sum(1 for q in queue if q.get("status") == "processing"),
            "total": len(queue),
        },
        "naver": {
            "cookie_exists": naver_playwright.cookies_exist(),
            "blog_id": NAVER_BLOG_ID or "(미설정)",
        },
        "tistory": {blog_id: tistory_pw.cookies_exist(blog_id) for blog_id in TISTORY_BLOGS},
        "accounts": _load_accounts(),
        "model": {
            "current": current_model_id,
            "key": model_key,
        },
    })


@app.route("/api/model", methods=["GET", "POST"])
def api_model():
    """AI 모델 조회/변경."""
    if request.method == "GET":
        current = _get_model()
        key = next((k for k, v in AVAILABLE_MODELS.items() if v == current), "haiku")
        return jsonify({"model": current, "key": key, "available": AVAILABLE_MODELS})

    data = request.get_json()
    model_key = data.get("model", "")
    if model_key not in AVAILABLE_MODELS:
        return jsonify({"error": f"지원하지 않는 모델: {model_key}"}), 400

    _save_env_value("AI_MODEL", AVAILABLE_MODELS[model_key])
    return jsonify({"ok": True, "model": AVAILABLE_MODELS[model_key], "key": model_key})


@app.route("/api/dashboard/scheduler", methods=["POST"])
def api_toggle_scheduler():
    """스케줄러 ON/OFF 토글."""
    data = request.get_json()
    config = _load_json(_CONFIG_PATH, _default_config())
    if "enabled" in data:
        config["enabled"] = bool(data["enabled"])
    if "min_interval_min" in data:
        config["min_interval_min"] = max(10, int(data["min_interval_min"]))
    if "max_interval_min" in data:
        config["max_interval_min"] = max(config["min_interval_min"], int(data["max_interval_min"]))
    _save_json(_CONFIG_PATH, config)

    # 스케줄러 모듈에 알림
    try:
        import scheduler as sched_mod
        sched_mod.toggle_scheduler(config["enabled"])
    except Exception:
        pass

    return jsonify({"success": True, "scheduler": config})


@app.route("/api/dashboard/run-once", methods=["POST"])
def api_run_once():
    """즉시 1건 발행."""
    try:
        import scheduler as sched_mod
        result = sched_mod.run_single()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/dashboard/logs")
def api_dashboard_logs():
    """발행 로그 조회."""
    logs = _load_json(_LOG_PATH, [])
    limit = request.args.get("limit", 50, type=int)
    errors_only = request.args.get("errors_only", "false") == "true"
    if errors_only:
        logs = [l for l in logs if l.get("status") != "success"]
    return jsonify({"logs": logs[-limit:][::-1]})


# ── 키워드 큐 API ──

@app.route("/api/queue")
def api_queue_list():
    """키워드 큐 목록."""
    queue = _load_json(_QUEUE_PATH, [])
    return jsonify({"queue": queue})


@app.route("/api/queue", methods=["POST"])
def api_queue_add():
    """키워드 추가."""
    data = request.get_json()
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "키워드를 입력해주세요."}), 400

    queue = _load_json(_QUEUE_PATH, [])
    entry = {
        "id": str(_uuid.uuid4())[:8],
        "keyword": keyword,
        "category": data.get("category", "it"),
        "platform": data.get("platform", "naver"),
        "tone": data.get("tone", "informative"),
        "status": "pending",
        "added_at": datetime.now().isoformat(),
        "published_at": None,
        "error": None,
        "article_title": None,
        "post_url": None,
    }
    queue.append(entry)
    _save_json(_QUEUE_PATH, queue)
    return jsonify({"success": True, "entry": entry})


@app.route("/api/queue/bulk", methods=["POST"])
def api_queue_bulk_add():
    """키워드 일괄 추가."""
    data = request.get_json()
    keywords_text = data.get("keywords", "")
    category = data.get("category", "it")
    platform = data.get("platform", "naver")
    tone = data.get("tone", "informative")

    keywords = [k.strip() for k in keywords_text.split("\n") if k.strip()]
    if not keywords:
        return jsonify({"error": "키워드를 입력해주세요."}), 400

    queue = _load_json(_QUEUE_PATH, [])
    added = []
    for kw in keywords:
        entry = {
            "id": str(_uuid.uuid4())[:8],
            "keyword": kw,
            "category": category,
            "platform": platform,
            "tone": tone,
            "status": "pending",
            "added_at": datetime.now().isoformat(),
            "published_at": None,
            "error": None,
            "article_title": None,
            "post_url": None,
        }
        queue.append(entry)
        added.append(entry)
    _save_json(_QUEUE_PATH, queue)
    return jsonify({"success": True, "added": len(added)})


@app.route("/api/queue/<entry_id>", methods=["DELETE"])
def api_queue_delete(entry_id):
    """키워드 삭제."""
    queue = _load_json(_QUEUE_PATH, [])
    queue = [q for q in queue if q["id"] != entry_id]
    _save_json(_QUEUE_PATH, queue)
    return jsonify({"success": True})


@app.route("/api/queue/<entry_id>/retry", methods=["POST"])
def api_queue_retry(entry_id):
    """실패 키워드 재시도."""
    queue = _load_json(_QUEUE_PATH, [])
    for q in queue:
        if q["id"] == entry_id:
            q["status"] = "pending"
            q["error"] = None
            break
    _save_json(_QUEUE_PATH, queue)
    return jsonify({"success": True})


@app.route("/api/queue/<entry_id>/priority", methods=["POST"])
def api_queue_priority(entry_id):
    """키워드 우선순위 올리기 (맨 앞으로)."""
    queue = _load_json(_QUEUE_PATH, [])
    target = None
    rest = []
    for q in queue:
        if q["id"] == entry_id:
            target = q
        else:
            rest.append(q)
    if target:
        queue = [target] + rest
        _save_json(_QUEUE_PATH, queue)
    return jsonify({"success": True})


# ──────────────────────────────────────────────
# 계정 관리 API
# ──────────────────────────────────────────────

@app.route("/api/accounts", methods=["POST"])
def api_accounts_add():
    """계정 추가."""
    data = request.get_json()
    platform = data.get("platform", "")
    url = data.get("url", "").strip().rstrip("/")
    if not platform or not url:
        return jsonify({"error": "플랫폼과 URL이 필요합니다."}), 400

    # URL에서 blog_id 추출
    if platform == "tistory":
        blog_id = url.replace("https://", "").replace("http://", "").split(".")[0]
        if not url.endswith(".tistory.com"):
            url = f"https://{blog_id}.tistory.com"
    elif platform == "naver":
        blog_id = url.replace("https://blog.naver.com/", "").replace("http://", "").split("/")[0].split(".")[0]
        url = f"https://blog.naver.com/{blog_id}"
    else:
        blog_id = url.replace("https://", "").replace("http://", "").split("/")[0]

    accounts = _load_accounts()
    if platform not in accounts:
        accounts[platform] = []

    # 중복 확인
    if any(a["id"] == blog_id for a in accounts[platform]):
        return jsonify({"error": "이미 등록된 계정입니다."}), 400

    accounts[platform].append({
        "id": blog_id,
        "name": blog_id,
        "url": url,
        "blog_id": blog_id,
    })
    _save_json(_ACCOUNTS_PATH, accounts)
    return jsonify({"success": True})


@app.route("/api/accounts/<entry_id>", methods=["DELETE"])
def api_accounts_delete(entry_id):
    """계정 삭제."""
    accounts = _load_accounts()
    for platform in accounts:
        accounts[platform] = [a for a in accounts[platform] if a["id"] != entry_id]
    _save_json(_ACCOUNTS_PATH, accounts)
    return jsonify({"success": True})


@app.route("/api/accounts/<entry_id>/publish", methods=["POST"])
def api_accounts_publish(entry_id):
    """계정에 즉시 발행 (큐의 첫 번째 pending 키워드 사용)."""
    accounts = _load_accounts()
    target = None
    target_platform = None
    for platform, accts in accounts.items():
        for a in accts:
            if a["id"] == entry_id:
                target = a
                target_platform = platform
                break

    if not target:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    # 큐에서 해당 플랫폼의 첫 번째 pending 키워드 찾기
    queue = _load_json(_QUEUE_PATH, [])
    keyword_entry = None
    for q in queue:
        if q.get("status") == "pending" and q.get("platform") == target_platform:
            keyword_entry = q
            break

    if not keyword_entry:
        # 플랫폼 무관하게 아무 pending이라도
        for q in queue:
            if q.get("status") == "pending":
                keyword_entry = q
                break

    if not keyword_entry:
        return jsonify({"error": "대기 중인 키워드가 없습니다. 키워드 큐에 먼저 추가하세요."}), 400

    # 스케줄러의 run_single 호출
    try:
        import scheduler as sched_mod
        result = sched_mod.run_single(keyword_entry["id"])
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────
# 스케줄러 초기화
# ──────────────────────────────────────────────

def _init_scheduler():
    """앱 시작 시 스케줄러 초기화 (Gunicorn worker 중 하나만 실행)."""
    try:
        import scheduler as sched_mod
        sched_mod.init_scheduler(app)
    except Exception as e:
        print(f"[Scheduler] 초기화 실패: {e}")

# Gunicorn에서는 worker가 fork된 후 실행
_init_scheduler()


if __name__ == "__main__":
    os.chdir(_APP_DIR)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))
