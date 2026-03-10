"""키워드 자동 수집 Blueprint
- 구글 자동완성 + 연관검색어 (초성/알파벳 확장)
- 네이버 자동완성 (검색 페이지 추천 키워드) + 연관검색어
- 네이버 검색광고 API 월간 검색량 조회
- 중복/유사 키워드 자동 정제
"""

import re
import os
import time
import hmac
import hashlib
import base64
import requests
from datetime import datetime
from flask import current_app
from flask import Blueprint, render_template, request, jsonify
from bs4 import BeautifulSoup

keywords_bp = Blueprint("keywords", __name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

_NAVER_NOISE = frozenset({"열기", "더보기", "닫기", "관련검색어", "연관검색어", "이전", "다음"})



# ──────────────────────────────────────────────
#  네이버 검색광고 API 인증
# ──────────────────────────────────────────────
def _naver_ad_signature(timestamp, method, path):
    """HMAC-SHA256 서명 생성"""
    secret = os.environ.get("NAVER_AD_SECRET_KEY", "")
    message = f"{timestamp}.{method}.{path}"
    sign = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(sign.digest()).decode("utf-8")


def _naver_ad_headers(method, path):
    """네이버 검색광고 API 인증 헤더 생성"""
    timestamp = str(int(time.time() * 1000))
    return {
        "X-Timestamp": timestamp,
        "X-API-KEY": os.environ.get("NAVER_AD_API_KEY", ""),
        "X-Customer": os.environ.get("NAVER_AD_CUSTOMER_ID", ""),
        "X-Signature": _naver_ad_signature(timestamp, method, path),
        "Content-Type": "application/json",
    }


# ──────────────────────────────────────────────
#  네이버 검색광고 API: 월간 검색량 조회
# ──────────────────────────────────────────────
def _get_search_volume(keywords):
    """키워드 리스트의 월간 검색량(PC+모바일) 조회.
    Returns: {keyword: {"pc": int, "mobile": int, "total": int}, ...}
    """
    if not keywords:
        return {}

    api_key = os.environ.get("NAVER_AD_API_KEY", "")
    if not api_key:
        return {}

    result = {}
    for idx, kw in enumerate(keywords):
        if idx > 0:
            time.sleep(0.3)
        path = "/keywordstool"
        headers = _naver_ad_headers("GET", path)
        query_kw = kw.replace(" ", "")
        try:
            resp = requests.get(
                "https://api.naver.com" + path,
                params={"hintKeywords": query_kw, "showDetail": "1"},
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("keywordList", []):
                    rel = item.get("relKeyword", "")
                    if rel.replace(" ", "").lower() != query_kw.lower():
                        continue
                    pc = item.get("monthlyPcQcCnt", 0)
                    mobile = item.get("monthlyMobileQcCnt", 0)
                    if isinstance(pc, str):
                        pc = 0
                    if isinstance(mobile, str):
                        mobile = 0
                    result[kw] = {"pc": pc, "mobile": mobile, "total": pc + mobile}
                    break
            elif resp.status_code == 429:
                print("[Keywords] 네이버 검색광고 API rate limit, 대기 후 재시도")
                time.sleep(3)
            else:
                print(f"[Keywords] 네이버 검색광고 API 에러: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"[Keywords] 네이버 검색광고 API 에러: {e}")

    return result

# ──────────────────────────────────────────────
#  페이지
# ──────────────────────────────────────────────
@keywords_bp.route("/keywords")
def keywords_page():
    return render_template("keywords.html")


# ──────────────────────────────────────────────
#  API: 주제 추천 (AI)
# ──────────────────────────────────────────────
@keywords_bp.route("/api/keywords/suggest-topics", methods=["POST"])
def suggest_topics():
    """카테고리 기반 블로그 주제 추천"""
    data = request.get_json(force=True)
    category = data.get("category", "").strip()
    if not category:
        return jsonify({"error": "카테고리를 선택하세요"}), 400

    month = datetime.now().month

    prompt = f"""{category} 블로그 주제 중 한국인들이 많이 검색하는 세부주제 10개를 추천해줘.
롱테일 키워드 잡기 좋은 것 위주로.
현재 {month}월 시즌에 맞는 주제 우선.
JSON 배열로만 반환해줘."""

    try:
        from app import claude_client, _get_model
        model = _get_model()
        resp = claude_client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # JSON 파싱 (코드펜스 제거)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        import json
        topics = json.loads(text)
        if not isinstance(topics, list):
            topics = []
        return jsonify({"category": category, "topics": topics[:10]})
    except Exception as e:
        print(f"[Keywords] 주제 추천 에러: {e}")
        return jsonify({"error": "주제 추천에 실패했습니다"}), 500


# ──────────────────────────────────────────────
#  API: 검색량 단독 조회
# ──────────────────────────────────────────────
@keywords_bp.route("/api/keywords/search-volume", methods=["POST"])
def search_volume_api():
    """키워드 리스트의 월간 검색량 조회"""
    data = request.get_json(force=True)
    keywords = data.get("keywords", [])
    min_volume = data.get("min_volume", 0)

    if not keywords:
        return jsonify({"error": "키워드를 입력하세요"}), 400

    volumes = _get_search_volume(keywords)
    items = []
    for kw in keywords:
        vol = volumes.get(kw, {"pc": 0, "mobile": 0, "total": 0})
        if vol["total"] >= min_volume:
            items.append({"keyword": kw, **vol})

    items.sort(key=lambda x: x["total"], reverse=True)
    return jsonify({"items": items, "total": len(items)})

# ──────────────────────────────────────────────
#  API: 통합 키워드 수집
# ──────────────────────────────────────────────
@keywords_bp.route("/api/keywords/collect", methods=["POST"])
def collect_keywords():
    data = request.get_json(force=True)
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "키워드를 입력하세요"}), 400

    results = {
        "google_autocomplete": _google_autocomplete(keyword),
        "google_related": _google_related(keyword),
        "naver_autocomplete": _naver_autocomplete(keyword),
        "naver_related": _naver_related(keyword),
    }

    all_kws = set()
    for kws in results.values():
        all_kws.update(kws)

    # Fallback: 결과가 너무 적으면 키워드를 축약해서 재수집
    if len(all_kws) < 5 and len(keyword.split()) > 2:
        short = _shorten_keyword(keyword)
        if short and short.lower() != keyword.lower():
            fb = {
                "google_autocomplete": _google_autocomplete(short),
                "naver_autocomplete": _naver_autocomplete(short),
            }
            for src, kws in fb.items():
                results.setdefault(src, [])
                existing = set(results[src])
                for kw in kws:
                    if kw not in existing:
                        results[src].append(kw)
                all_kws.update(kws)

    refined = _deduplicate(list(all_kws), keyword)

    # 검색량 조회
    volumes = _get_search_volume(refined)
    refined_with_vol = []
    for kw in refined:
        vol = volumes.get(kw, {"pc": 0, "mobile": 0, "total": 0})
        refined_with_vol.append({"keyword": kw, **vol})

    # 검색량 높은 순 정렬
    refined_with_vol.sort(key=lambda x: x["total"], reverse=True)

    return jsonify({
        "keyword": keyword,
        "sources": results,
        "total_raw": len(all_kws),
        "refined": refined_with_vol,
        "total_refined": len(refined_with_vol),
    })


# ──────────────────────────────────────────────
#  구글 자동완성 (접미사 확장)
# ──────────────────────────────────────────────
def _google_autocomplete(keyword: str) -> list:
    results = []
    seen = set()
    suffixes = ["", " 추천", " 방법", " 비용", " 후기"]
    for suffix in suffixes:
        try:
            resp = requests.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "firefox", "q": keyword + suffix, "hl": "ko"},
                headers=_HEADERS,
                timeout=5,
            )
            if resp.status_code == 200:
                for s in resp.json()[1]:
                    if s.lower() != keyword.lower() and s not in seen:
                        seen.add(s)
                        results.append(s)
        except Exception as e:
            print(f"[Keywords] 구글 자동완성 에러: {e}")
    return results[:30]


# ──────────────────────────────────────────────
#  구글 연관검색어 (초성/알파벳 확장)
#  - 구글 검색 결과 페이지가 봇 차단되므로
#    자동완성 API에 한글 초성+알파벳 접미사를 붙여 확장 수집
# ──────────────────────────────────────────────
_EXPANSIONS = list(" ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ") + list("abcdefghij")

def _google_related(keyword: str) -> list:
    results = []
    seen = set()
    for ch in _EXPANSIONS:
        q = keyword + " " + ch if ch.strip() else keyword
        try:
            resp = requests.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "firefox", "q": q, "hl": "ko"},
                headers=_HEADERS,
                timeout=3,
            )
            if resp.status_code == 200:
                for s in resp.json()[1]:
                    if s.lower() != keyword.lower() and s not in seen:
                        seen.add(s)
                        results.append(s)
        except Exception:
            pass
    # 구글 자동완성과 겹치는 것 제거는 상위 _deduplicate에서 처리
    return results[:50]


# ──────────────────────────────────────────────
#  네이버 자동완성 (검색 페이지 추천 키워드 + 칩)
#  - ac.search.naver.com API가 로컬에서 빈 결과를 주므로
#    네이버 검색 결과 페이지에서 추천 키워드를 직접 파싱
# ──────────────────────────────────────────────
def _naver_autocomplete(keyword: str) -> list:
    results = []
    seen = set()

    # 1) 네이버 자동완성 API (st=100 파라미터 필수)
    suffixes = ["", " 추천", " 방법", " 비용", " 후기", " 비교", " 순위", " 정리", " 꿀팁"]
    for suffix in suffixes:
        try:
            resp = requests.get(
                "https://ac.search.naver.com/nx/ac",
                params={
                    "q": keyword + suffix, "con": "1", "frm": "nv", "ans": "2",
                    "r_format": "json", "r_enc": "UTF-8", "r_unicode": "0",
                    "t_koreng": "1", "run": "2", "rev": "4", "q_enc": "UTF-8",
                    "st": "100",
                },
                headers=_HEADERS,
                timeout=3,
            )
            if resp.status_code == 200:
                data = resp.json()
                for group in data.get("items", []):
                    for item in group:
                        text = item[0] if isinstance(item, list) else item
                        if isinstance(text, str) and text.lower() != keyword.lower() and text not in seen:
                            seen.add(text)
                            results.append(text)
        except Exception:
            pass

    # 2) API 결과가 부족하면 검색 페이지 추천 키워드로 보충
    if len(results) < 5:
        try:
            resp = requests.get(
                "https://search.naver.com/search.naver",
                params={"query": keyword, "where": "nexearch"},
                headers={**_HEADERS, "Referer": "https://search.naver.com/"},
                timeout=8,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup.select("[class*='keyword-'], [class*='chip']"):
                    text = tag.get_text(strip=True)
                    if _is_valid_naver_kw(text, keyword, seen):
                        seen.add(text)
                        results.append(text)
        except Exception:
            pass

    return results[:25]


# ──────────────────────────────────────────────
#  네이버 연관검색어
# ──────────────────────────────────────────────
def _naver_related(keyword: str) -> list:
    try:
        session = requests.Session()
        session.headers.update(_HEADERS)
        session.headers["Referer"] = "https://search.naver.com/"
        resp = session.get(
            "https://search.naver.com/search.naver",
            params={"query": keyword, "where": "nexearch"},
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        related = []
        seen = set()

        # 연관 검색어 영역: .lst_related_srch .keyword, .related_srch .keyword
        for el in soup.select("ul.lst_related_srch .keyword, div.related_srch .keyword, a.keyword"):
            text = el.get_text(strip=True)
            if _is_valid_naver_kw(text, keyword, seen):
                seen.add(text)
                related.append(text)

        # 보강: class에 'relate' 포함된 영역의 a 태그
        if not related:
            for el in soup.select("[class*='relate'] a"):
                text = el.get_text(strip=True)
                if _is_valid_naver_kw(text, keyword, seen):
                    seen.add(text)
                    related.append(text)

        return related[:15]
    except Exception as e:
        print(f"[Keywords] 네이버 연관검색어 에러: {e}")
    return []


def _is_valid_naver_kw(text: str, keyword: str, seen: set) -> bool:
    """네이버에서 추출한 텍스트가 유효한 키워드인지 판별"""
    if not text or len(text) < 2 or len(text) > 40:
        return False
    if text in seen or text in _NAVER_NOISE:
        return False
    if text.lower() == keyword.lower():
        return False
    # 긴 문장형 텍스트 필터 (블로그 본문 제목 등)
    if len(text) > 30 and (" " in text):
        return False
    return True


# ──────────────────────────────────────────────
#  긴 키워드 축약 (fallback용)
# ──────────────────────────────────────────────
_STOPWORDS = frozenset({"방법", "가이드", "추천", "비교", "후기", "정리", "총정리",
                        "종류", "순위", "best", "top", "how", "what", "the"})

def _shorten_keyword(keyword):
    """긴 키워드에서 핵심 단어 2~3개만 추출"""
    words = keyword.split()
    # 불용어 제거
    core = [w for w in words if w.lower() not in _STOPWORDS and len(w) > 1]
    if not core:
        core = words
    # 앞에서 2~3단어만
    return " ".join(core[:3]) if len(core) > 3 else " ".join(core)


# ──────────────────────────────────────────────
#  중복/유사 키워드 정제
# ──────────────────────────────────────────────
def _deduplicate(keywords: list[str], seed: str) -> list:
    if not keywords:
        return []

    seen_normalized = set()
    result = []

    for kw in keywords:
        kw = kw.strip()
        if not kw or len(kw) < 2:
            continue
        normalized = re.sub(r"\s+", " ", kw.lower().strip())
        stripped = re.sub(r"(은|는|이|가|을|를|의|에|도|로|으로)$", "", normalized)
        if stripped in seen_normalized or normalized in seen_normalized:
            continue
        seen_normalized.add(normalized)
        seen_normalized.add(stripped)
        result.append(kw)

    seed_norm = re.sub(r"\s+", " ", seed.lower().strip())
    result = [kw for kw in result if re.sub(r"\s+", " ", kw.lower().strip()) != seed_norm]
    result.sort(key=len)
    return result


# ──────────────────────────────────────────────
#  정부지원금 전용 키워드 수집
# ──────────────────────────────────────────────
_GOV_SUFFIXES = [
    " 지원금", " 보조금", " 신청방법", " 신청 자격", " 대상",
    " 지원 대상", " 신청 기간", " 지원 금액", " 조건",
]

_SEASONAL_KEYWORDS = {
    1:  ["연말정산 환급", "근로장려금 신청", "자녀장려금", "건강보험 정산", "주거급여 신청"],
    2:  ["설 명절 지원금", "긴급복지 신청", "국민취업지원제도", "청년내일저축계좌", "에너지바우처"],
    3:  ["청년지원금 2026", "청년도약계좌", "국민연금 실업크레딧", "청년월세지원", "소상공인 저금리 대출"],
    4:  ["근로장려금 신청기간", "자녀장려금 신청", "청년 주택드림 청약", "장애인 활동지원", "고용안정장려금"],
    5:  ["소상공인 지원금", "근로장려금 반기 신청", "어린이집 보육료 지원", "출산지원금", "산모 신생아 건강관리"],
    6:  ["여름 에너지 바우처", "청년 전세대출", "경력단절여성 지원", "실업급여 신청", "기초연금 인상"],
    7:  ["하반기 청년지원", "국민건강보험 환급", "재난지원금", "저소득층 여름 냉방비", "고용보험 피보험자격"],
    8:  ["2학기 국가장학금", "학자금 대출", "주거급여 재신청", "기초생활수급자 혜택", "다자녀 지원 혜택"],
    9:  ["추석 명절 지원금", "하반기 근로장려금", "청년 취업성공패키지", "노인일자리 사업", "긴급생활안정자금"],
    10: ["겨울 에너지바우처 신청", "연말정산 준비", "내년 정부 예산안", "소상공인 폐업지원", "고용촉진장려금"],
    11: ["연말정산 소득공제", "자동차 세금 감면", "건강보험료 경감", "기초연금 신청", "주거안정장학금"],
    12: ["연말정산 공제 항목", "차상위계층 혜택", "내년 최저임금", "청년도약계좌 가입", "긴급복지 지원"],
}


@keywords_bp.route("/api/keywords/government", methods=["POST"])
def collect_government_keywords():
    """정부지원금 전용 키워드 수집 API"""
    data = request.get_json(force=True)
    keyword = data.get("keyword", "").strip()

    # 시즌 키워드 추천
    current_month = datetime.now().month
    seasonal = _SEASONAL_KEYWORDS.get(current_month, [])

    if not keyword:
        return jsonify({
            "keyword": "",
            "seasonal": seasonal,
            "seasonal_month": current_month,
            "refined": [],
            "total_refined": 0,
        })

    # 기본 키워드 수집 + 정부지원금 접미사 확장
    gov_autocomplete = []
    seen = set()
    for suffix in _GOV_SUFFIXES:
        q = keyword + suffix
        try:
            resp = requests.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "firefox", "q": q, "hl": "ko"},
                headers=_HEADERS, timeout=5,
            )
            if resp.status_code == 200:
                for s in resp.json()[1]:
                    if s.lower() != keyword.lower() and s not in seen:
                        seen.add(s)
                        gov_autocomplete.append(s)
        except Exception:
            pass

    # 네이버 자동완성도 수집
    naver_gov = []
    for suffix in _GOV_SUFFIXES[:5]:
        try:
            resp = requests.get(
                "https://ac.search.naver.com/nx/ac",
                params={
                    "q": keyword + suffix, "con": "1", "frm": "nv", "ans": "2",
                    "r_format": "json", "r_enc": "UTF-8", "r_unicode": "0",
                    "t_koreng": "1", "run": "2", "rev": "4", "q_enc": "UTF-8",
                    "st": "100",
                },
                headers=_HEADERS, timeout=3,
            )
            if resp.status_code == 200:
                for group in resp.json().get("items", []):
                    for item in group:
                        text = item[0] if isinstance(item, list) else item
                        if isinstance(text, str) and text.lower() != keyword.lower() and text not in seen:
                            seen.add(text)
                            naver_gov.append(text)
        except Exception:
            pass

    all_kws = list(seen)
    refined = _deduplicate(all_kws, keyword)

    # 검색량 조회
    volumes = _get_search_volume(refined)
    refined_with_vol = []
    for kw in refined:
        vol = volumes.get(kw, {"pc": 0, "mobile": 0, "total": 0})
        refined_with_vol.append({"keyword": kw, **vol})

    refined_with_vol.sort(key=lambda x: x["total"], reverse=True)

    return jsonify({
        "keyword": keyword,
        "sources": {
            "google_government": gov_autocomplete[:30],
            "naver_government": naver_gov[:20],
        },
        "seasonal": seasonal,
        "seasonal_month": current_month,
        "total_raw": len(all_kws),
        "refined": refined_with_vol,
        "total_refined": len(refined_with_vol),
    })
