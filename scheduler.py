"""
자동 발행 스케줄러 모듈.

APScheduler를 사용하여 keyword_queue.json에서 대기 중인 키워드를 순서대로
글 생성 → 네이버 발행 → 로그 기록하는 파이프라인을 자동 실행합니다.

- 30~120분 랜덤 간격으로 실행 (설정 가능)
- KST 7시~23시 사이에만 실행
- 파일 잠금으로 Gunicorn 멀티 워커 중복 실행 방지
"""

import json
import logging
import os
import random
import re
import uuid
import fcntl
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

import anthropic

logger = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_APP_DIR, "data")
_LOCK_FILE = os.path.join(_DATA_DIR, ".scheduler.lock")
_QUEUE_FILE = os.path.join(_DATA_DIR, "keyword_queue.json")
_CONFIG_FILE = os.path.join(_DATA_DIR, "scheduler_config.json")
_LOG_FILE = os.path.join(_DATA_DIR, "publish_log.json")

KST = ZoneInfo("Asia/Seoul")

# 스케줄러 싱글턴
_scheduler: Optional[BackgroundScheduler] = None
_flask_app = None

# ──────────────────────────────────────────────
#  파일 잠금 헬퍼 (멀티 워커 안전)
# ──────────────────────────────────────────────

def _acquire_lock():
    """파일 기반 배타 잠금을 획득합니다. 실패 시 None 반환."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    try:
        fd = open(_LOCK_FILE, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, IOError):
        return None


def _release_lock(fd):
    """파일 잠금을 해제합니다."""
    if fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except Exception:
            pass


# ──────────────────────────────────────────────
#  JSON 파일 읽기/쓰기 (파일 잠금 포함)
# ──────────────────────────────────────────────

def _read_json(path, default=None):
    """JSON 파일을 읽습니다. 파일이 없거나 파싱 실패 시 default 반환."""
    if default is None:
        default = []
    if not os.path.exists(path):
        return default
    try:
        fd = open(path, "r", encoding="utf-8")
        fcntl.flock(fd, fcntl.LOCK_SH)
        try:
            data = json.load(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        return data
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    """JSON 파일을 씁니다 (배타 잠금)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = open(path, "w", encoding="utf-8")
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        json.dump(data, fd, ensure_ascii=False, indent=2)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def _append_log(entry: dict):
    """publish_log.json에 항목을 추가합니다."""
    logs = _read_json(_LOG_FILE, [])
    logs.append(entry)
    _write_json(_LOG_FILE, logs)


# ──────────────────────────────────────────────
#  스케줄러 설정
# ──────────────────────────────────────────────

_DEFAULT_CONFIG = {
    "enabled": False,
    "min_interval_min": 30,
    "max_interval_min": 120,
    "start_hour": 7,
    "end_hour": 23,
    "last_run_at": None,
    "next_run_at": None,
}


def _load_config() -> dict:
    """스케줄러 설정을 로드합니다."""
    config = _read_json(_CONFIG_FILE, {})
    merged = {**_DEFAULT_CONFIG, **config}
    return merged


def _save_config(config: dict):
    """스케줄러 설정을 저장합니다."""
    _write_json(_CONFIG_FILE, config)


# ──────────────────────────────────────────────
#  키워드 큐 관리
# ──────────────────────────────────────────────

def _get_next_pending(keyword_id: Optional[str] = None) -> Optional[dict]:
    """다음 대기 중인 키워드를 반환합니다.

    keyword_id가 지정되면 해당 키워드를, 아니면 첫 번째 pending을 반환합니다.
    """
    queue = _read_json(_QUEUE_FILE, [])
    for item in queue:
        if keyword_id and item.get("id") == keyword_id:
            if item.get("status") in ("pending", "processing"):
                return item
            return None
        if not keyword_id and item.get("status") == "pending":
            return item
    return None


def _update_keyword_status(keyword_id: str, updates: dict):
    """키워드 큐의 항목을 업데이트합니다."""
    queue = _read_json(_QUEUE_FILE, [])
    for item in queue:
        if item.get("id") == keyword_id:
            item.update(updates)
            break
    _write_json(_QUEUE_FILE, queue)


# ──────────────────────────────────────────────
#  글 생성 (app.py 로직 재현)
# ──────────────────────────────────────────────

def generate_article(keyword: str, platform: str = "naver",
                     category: str = "it", tone: str = "informative",
                     subtype: str = "") -> dict:
    """키워드로 블로그 글을 생성합니다.

    app.py의 /generate 엔드포인트 로직을 독립 함수로 구현합니다.

    Returns:
        {"success": True, "title": ..., "body": ..., "tags": ..., ...}
        또는 {"success": False, "error": "..."}
    """
    from app import (
        claude_client, _get_model,
        SEO_PROMPTS_IT, SEO_PROMPTS_TRAVEL, SEO_PROMPTS_LIVING,
        SEO_PROMPTS_GOVERNMENT, PLATFORM_NAMES,
        ADSENSE_TISTORY, ADSENSE_IT, GOVERNMENT_DISCLAIMER,
        _search_unsplash_images, _insert_adsense, _insert_adsense_3,
        _generate_body_with_web_search,
    )

    if not keyword:
        return {"success": False, "error": "키워드가 비어 있습니다."}

    # 카테고리별 프롬프트 선택
    if category == "travel":
        seo_prompts = SEO_PROMPTS_TRAVEL
        if platform == "blogspot":
            return {"success": False, "error": "여행 카테고리는 블로그스팟을 지원하지 않습니다."}
    elif category == "living":
        seo_prompts = SEO_PROMPTS_LIVING
        if platform != "naver":
            return {"success": False, "error": "살림/생활 카테고리는 네이버 블로그만 지원합니다."}
    elif category == "government":
        seo_prompts = SEO_PROMPTS_GOVERNMENT
        if platform == "blogspot":
            return {"success": False, "error": "정부지원금 카테고리는 블로그스팟을 지원하지 않습니다."}
    else:
        seo_prompts = SEO_PROMPTS_IT

    if platform not in seo_prompts:
        return {"success": False, "error": "지원하지 않는 플랫폼입니다."}

    tone_map = {
        "informative": "정보 전달형 (객관적이고 신뢰감 있는 톤)",
        "experience": "체험형 (1인칭 경험담, 솔직하고 생생한 톤)",
        "casual": "일상 대화형 (친근하고 편안한 톤)",
        "professional": "전문가형 (권위 있고 깊이 있는 톤)",
    }
    tone_desc = tone_map.get(tone, tone_map["informative"])
    use_model = _get_model()

    system_prompt = seo_prompts[platform]
    system_prompt += f"\n\n현재 연도는 {datetime.now().year}년입니다. 제목과 본문에 연도가 필요하면 반드시 이 연도를 사용하세요."

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

    # 태그 수 및 사용자 프롬프트
    if category == "living":
        tag_count = 20
    else:
        tag_count = 10

    # ── 1단계: 제목·태그 생성 ──
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
            "2. 본문에 쓸 대표 제목은 체험형 제목으로 정하세요\n"
            f"3. 태그는 반드시 {tag_count}개를 생성하세요 (인기 태그 10개 + 세부 틈새 태그 10개)\n\n"
            "응답 형식:\n"
            "---제목---\n(체험형 대표 제목)\n---태그---\n(쉼표로 구분된 태그 20개)\n"
            "---제목후보---\n(체험형: / 정보형: / 질문형: 각 한 줄씩)"
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
        return {"success": False, "error": f"제목/태그 생성 실패: {e.message}"}

    # 제목·태그 파싱
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

    # ── 2단계: 본문 생성 ──
    use_web_search = (category == "government" or subtype == "walkthrough")

    body_prompt = (
        f"다음 제목과 키워드로 {PLATFORM_NAMES[platform]} 블로그 본문만 작성해주세요.\n\n"
        f"제목: {title}\n"
        f"키워드: {keyword}\n"
        f"글 톤: {tone_desc}\n\n"
    )

    meta_desc_instruction = ""
    if platform == "tistory":
        meta_desc_instruction = (
            "\n\n응답 형식:\n"
            "HTML 본문을 먼저 출력하고, 마지막에 아래 형식으로 메타설명을 추가하세요.\n"
            "---메타설명---\n"
            "150~160자의 검색 결과 미리보기용 메타 설명"
        )

    if use_web_search:
        if category == "government":
            body_prompt += (
                "요구사항:\n"
                "1. 웹 검색으로 복지로, 정부24 등 공식 사이트에서 이 정책의 최신 정보를 확인하세요\n"
                "2. 지원 대상, 금액, 신청 기간, 신청 방법, 필요 서류를 반드시 포함하세요\n"
                "3. 확인되지 않은 정보는 '공식 사이트에서 확인 필요'로 표기하세요\n"
                "4. 본문은 HTML 형식으로 작성하세요\n"
                "5. 이미지는 자동 삽입되므로 [이미지] 같은 플레이스홀더를 넣지 마세요\n"
                "6. HTML table 태그로 지원 조건/금액 비교표를 포함하세요\n"
                "7. 광고 플레이스홀더 절대 금지, 광고는 자동 삽입됩니다\n"
                "8. 면책 문구는 자동 삽입되므로 본문에 넣지 마세요\n"
            )
        else:
            body_prompt += (
                "요구사항:\n"
                "1. 먼저 웹 검색을 사용하여 이 키워드에 대한 최신 공략/가이드 정보를 검색하세요\n"
                "2. 검색 결과를 바탕으로 정확하고 최신의 공략 정보를 포함한 본문을 작성하세요\n"
                "3. 본문은 HTML 형식으로 작성하세요\n"
                "4. 이미지는 자동 삽입되므로 [이미지] 같은 플레이스홀더를 넣지 마세요\n"
                "5. HTML table 태그 사용 시 모든 셀에 반드시 구체적인 내용을 채우세요\n"
                "6. 광고 플레이스홀더 절대 금지, 광고는 자동 삽입됩니다\n"
                "7. 검색 결과의 출처를 적절히 참고하되, 글은 독창적으로 재구성하세요\n"
            )
        if not meta_desc_instruction:
            body_prompt += "\n응답: HTML 본문만 출력하세요. 제목이나 태그는 포함하지 마세요."
        else:
            body_prompt += meta_desc_instruction
    else:
        body_prompt += (
            "요구사항:\n"
            "1. 본문은 HTML 형식으로 작성하세요\n"
            "2. 이미지는 자동 삽입되므로 [이미지] 같은 플레이스홀더를 넣지 마세요\n"
            "3. HTML table 태그 사용 시 모든 셀에 반드시 구체적인 내용을 채우세요\n"
            "4. 광고 플레이스홀더(<!-- 광고위치 -->, [광고], Advertisement div) 절대 금지\n"
            "5. 광고는 자동 삽입되므로 본문에 광고 관련 코드를 넣지 마세요\n"
        )
        if not meta_desc_instruction:
            body_prompt += "\n응답: HTML 본문만 출력하세요. 제목이나 태그는 포함하지 마세요."
        else:
            body_prompt += meta_desc_instruction

    # 구글 SEO 필수 규칙
    body_prompt += (
        "\n\n[구글 SEO 필수 규칙]\n"
        "1. 도입부 필수: 본문은 반드시 도입부(3~5문장)로 시작할 것. "
        "독자의 관심을 끄는 질문이나 공감 문장으로 시작하고, "
        f"포커스 키워드(\"{keyword}\")를 도입부 안에 자연스럽게 포함시킬 것. "
        "도입부를 생략하고 바로 정보나 표로 시작하지 말 것.\n"
        "2. H태그 구조 준수: H2는 주요 섹션 제목, H3는 세부 항목(신청자격, 소득기준, 지원금액 등)\n"
        "3. 표(Table) 사용 시: 표 바로 위에 캡션 문장 추가(예: \"2026년 청년지원금 주요 항목 비교표\"), 글 상단에 요약표 배치\n"
        "4. 이미지 삽입 시 alt 태그에 포커스 키워드 포함\n"
        f"5. 본문 내 포커스 키워드(\"{keyword}\")를 자연스럽게 3회 이상 포함\n"
        "6. 글 하단에 내부 링크 유도 문구 1개 추가(예: \"관련 정보가 궁금하다면 아래 글도 확인해보세요\")\n"
        "7. 소득기준 등 핵심 수치는 <blockquote> 인용구 블록으로 강조\n"
    )

    try:
        if use_web_search:
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
        return {"success": False, "error": f"본문 생성 실패: {e.message}"}

    # 메타설명 파싱 (티스토리)
    meta_description = ""
    if platform == "tistory" and "---메타설명---" in body:
        body, meta_desc_raw = body.split("---메타설명---", 1)
        meta_description = meta_desc_raw.strip()
        body = body.strip()

    # 본문 후처리
    body = re.sub(r'^```\w*\n?', '', body)
    body = re.sub(r'\n?```$', '', body)
    body = body.strip()
    body = re.sub(r"\[이미지[^\]]*\]", "", body)
    body = re.sub(r"<!--\s*광고[^>]*-->", "", body)
    body = re.sub(r'<div[^>]*>\s*Advertisement\s*</div>', "", body, flags=re.IGNORECASE)
    body = re.sub(r'<script\s+type=["\']application/ld\+json["\']>.*?</script>', "", body, flags=re.DOTALL | re.IGNORECASE)

    # Unsplash 대표 이미지
    thumbnail_url = ""
    try:
        all_images = _search_unsplash_images(keyword, 1, title=title)
        if all_images:
            thumbnail_url = all_images[0]["url"].split("?")[0] + "?w=800&h=800&fit=crop&fm=webp&q=80"
    except Exception:
        pass

    # 제목 주석
    body = f"<!-- 제목: {title} -->\n" + body

    # 플랫폼별 AdSense 광고 삽입 (네이버는 미지원)
    if platform != "naver":
        if platform == "tistory":
            ad_code = ADSENSE_TISTORY
            body = _insert_adsense_3(body, ad_code)
        else:
            ad_code = ADSENSE_IT
            body = _insert_adsense(body, ad_code)

    # 정부지원금 면책 문구
    if category == "government":
        body += "\n" + GOVERNMENT_DISCLAIMER

    result = {
        "success": True,
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

    return result


# ──────────────────────────────────────────────
#  발행 파이프라인
# ──────────────────────────────────────────────

def _publish_pipeline(keyword_entry: dict) -> dict:
    """단일 키워드에 대해 글 생성 → 네이버 발행 → 로그 기록을 수행합니다.

    Returns:
        {"success": True/False, ...} 발행 결과
    """
    import naver_playwright

    keyword_id = keyword_entry["id"]
    keyword = keyword_entry["keyword"]
    category = keyword_entry.get("category", "it")
    platform = keyword_entry.get("platform", "naver")
    tone = keyword_entry.get("tone", "informative")

    now_kst = datetime.now(KST).isoformat()
    log_entry = {
        "keyword_id": keyword_id,
        "keyword": keyword,
        "category": category,
        "platform": platform,
        "started_at": now_kst,
        "finished_at": None,
        "success": False,
        "error": None,
        "article_title": None,
        "post_url": None,
    }

    # 상태를 processing으로 변경
    _update_keyword_status(keyword_id, {"status": "processing"})

    # 1) 글 생성
    logger.info(f"[스케줄러] 글 생성 시작: {keyword} (카테고리: {category})")
    article = generate_article(keyword, platform=platform, category=category, tone=tone)

    if not article.get("success"):
        error_msg = article.get("error", "글 생성 실패")
        logger.error(f"[스케줄러] 글 생성 실패: {keyword} - {error_msg}")
        _update_keyword_status(keyword_id, {
            "status": "failed",
            "error": error_msg,
        })
        log_entry["error"] = error_msg
        log_entry["finished_at"] = datetime.now(KST).isoformat()
        _append_log(log_entry)
        return {"success": False, "error": error_msg}

    title = article["title"]
    body = article["body"]
    tags_str = article.get("tags", "")
    tags_list = [t.strip() for t in tags_str.split(",") if t.strip()][:10]

    log_entry["article_title"] = title
    logger.info(f"[스케줄러] 글 생성 완료: {title}")

    # 2) 네이버 발행
    logger.info(f"[스케줄러] 네이버 발행 시작: {title}")
    try:
        pub_result = naver_playwright.publish_to_naver(title, body, tags_list)
    except Exception as e:
        error_msg = f"네이버 발행 예외: {str(e)}"
        logger.error(f"[스케줄러] {error_msg}")
        _update_keyword_status(keyword_id, {
            "status": "failed",
            "error": error_msg,
        })
        log_entry["error"] = error_msg
        log_entry["finished_at"] = datetime.now(KST).isoformat()
        _append_log(log_entry)
        return {"success": False, "error": error_msg}

    if not pub_result.get("success"):
        error_msg = pub_result.get("error", "네이버 발행 실패")
        logger.error(f"[스케줄러] 네이버 발행 실패: {error_msg}")
        _update_keyword_status(keyword_id, {
            "status": "failed",
            "error": error_msg,
        })
        log_entry["error"] = error_msg
        log_entry["finished_at"] = datetime.now(KST).isoformat()
        _append_log(log_entry)
        return {"success": False, "error": error_msg}

    post_url = pub_result.get("post_url", "")
    logger.info(f"[스케줄러] 네이버 발행 성공: {post_url}")

    # 3) 상태 업데이트 및 로그 기록
    _update_keyword_status(keyword_id, {
        "status": "published",
        "published_at": datetime.now(KST).isoformat(),
        "article_title": title,
        "post_url": post_url,
    })

    log_entry["success"] = True
    log_entry["post_url"] = post_url
    log_entry["finished_at"] = datetime.now(KST).isoformat()
    _append_log(log_entry)

    return {"success": True, "title": title, "post_url": post_url}


# ──────────────────────────────────────────────
#  스케줄러 작업 함수
# ──────────────────────────────────────────────

def _scheduled_job():
    """스케줄러가 호출하는 메인 작업 함수.

    - 파일 잠금으로 단일 워커만 실행
    - KST 시간대 체크
    - 다음 실행 스케줄링
    """
    config = _load_config()
    if not config.get("enabled"):
        return

    # KST 시간대 체크
    now_kst = datetime.now(KST)
    start_hour = config.get("start_hour", 7)
    end_hour = config.get("end_hour", 23)
    if now_kst.hour < start_hour or now_kst.hour >= end_hour:
        logger.info(f"[스케줄러] 운영 시간 외 (현재 {now_kst.hour}시, {start_hour}~{end_hour}시만 운영)")
        _schedule_next()
        return

    # 파일 잠금 획득 (단일 워커만)
    lock_fd = _acquire_lock()
    if lock_fd is None:
        logger.debug("[스케줄러] 다른 워커가 실행 중, 건너뜀")
        return

    try:
        # 다음 대기 키워드 가져오기
        keyword_entry = _get_next_pending()
        if not keyword_entry:
            logger.info("[스케줄러] 대기 중인 키워드 없음")
            _schedule_next()
            return

        # 발행 파이프라인 실행
        result = _publish_pipeline(keyword_entry)

        # 설정 업데이트
        config["last_run_at"] = datetime.now(KST).isoformat()
        _save_config(config)

        if result.get("success"):
            logger.info(f"[스케줄러] 발행 완료: {result.get('title')}")
        else:
            logger.warning(f"[스케줄러] 발행 실패: {result.get('error')}")

    except Exception as e:
        logger.error(f"[스케줄러] 예기치 않은 오류: {e}", exc_info=True)
    finally:
        _release_lock(lock_fd)

    # 다음 실행 예약
    _schedule_next()


def _schedule_next():
    """다음 실행을 랜덤 간격으로 예약합니다."""
    global _scheduler
    if _scheduler is None:
        return

    config = _load_config()
    if not config.get("enabled"):
        return

    min_min = config.get("min_interval_min", 30)
    max_min = config.get("max_interval_min", 120)
    interval = random.randint(min_min, max_min)

    next_run = datetime.now(KST) + timedelta(minutes=interval)

    # 운영 시간 체크: 다음 실행이 운영 시간 밖이면 다음 날 시작 시간으로
    start_hour = config.get("start_hour", 7)
    end_hour = config.get("end_hour", 23)
    if next_run.hour >= end_hour:
        next_run = next_run.replace(hour=start_hour, minute=random.randint(0, 30),
                                     second=0, microsecond=0)
        next_run += timedelta(days=1)
    elif next_run.hour < start_hour:
        next_run = next_run.replace(hour=start_hour, minute=random.randint(0, 30),
                                     second=0, microsecond=0)

    # 기존 작업 제거 후 새로 예약
    try:
        _scheduler.remove_job("auto_publish")
    except Exception:
        pass

    _scheduler.add_job(
        _scheduled_job,
        "date",
        run_date=next_run,
        id="auto_publish",
        replace_existing=True,
    )

    config["next_run_at"] = next_run.isoformat()
    _save_config(config)
    logger.info(f"[스케줄러] 다음 실행 예약: {next_run.strftime('%Y-%m-%d %H:%M KST')} ({interval}분 후)")


# ──────────────────────────────────────────────
#  공개 API
# ──────────────────────────────────────────────

def init_scheduler(app):
    """Flask 앱과 함께 스케줄러를 초기화합니다.

    app.py에서 호출:
        from scheduler import init_scheduler
        init_scheduler(app)
    """
    global _scheduler, _flask_app
    _flask_app = app

    os.makedirs(_DATA_DIR, exist_ok=True)

    # 설정 파일 초기화
    if not os.path.exists(_CONFIG_FILE):
        _save_config(_DEFAULT_CONFIG)

    _scheduler = BackgroundScheduler(timezone=KST)
    _scheduler.start()

    config = _load_config()
    if config.get("enabled"):
        logger.info("[스케줄러] 활성 상태 — 첫 작업 예약 중")
        _schedule_next()
    else:
        logger.info("[스케줄러] 비활성 상태 — 수동으로 활성화하세요")


def get_status() -> dict:
    """스케줄러 상태를 반환합니다 (대시보드 API용)."""
    config = _load_config()
    queue = _read_json(_QUEUE_FILE, [])

    pending_count = sum(1 for item in queue if item.get("status") == "pending")
    published_count = sum(1 for item in queue if item.get("status") == "published")
    failed_count = sum(1 for item in queue if item.get("status") == "failed")

    # 최근 로그 5건
    logs = _read_json(_LOG_FILE, [])
    recent_logs = logs[-5:] if logs else []

    return {
        "enabled": config.get("enabled", False),
        "min_interval_min": config.get("min_interval_min", 30),
        "max_interval_min": config.get("max_interval_min", 120),
        "start_hour": config.get("start_hour", 7),
        "end_hour": config.get("end_hour", 23),
        "last_run_at": config.get("last_run_at"),
        "next_run_at": config.get("next_run_at"),
        "queue_stats": {
            "pending": pending_count,
            "published": published_count,
            "failed": failed_count,
            "total": len(queue),
        },
        "recent_logs": recent_logs,
    }


def toggle_scheduler(enabled: bool) -> dict:
    """스케줄러를 켜거나 끕니다.

    Args:
        enabled: True면 활성화, False면 비활성화

    Returns:
        {"enabled": bool, "next_run_at": str|None}
    """
    global _scheduler

    config = _load_config()
    config["enabled"] = enabled
    _save_config(config)

    if enabled:
        if _scheduler is None:
            _scheduler = BackgroundScheduler(timezone=KST)
            _scheduler.start()
        _schedule_next()
        config = _load_config()
        logger.info("[스케줄러] 활성화됨")
        return {"enabled": True, "next_run_at": config.get("next_run_at")}
    else:
        # 예약된 작업 제거
        if _scheduler:
            try:
                _scheduler.remove_job("auto_publish")
            except Exception:
                pass
        config["next_run_at"] = None
        _save_config(config)
        logger.info("[스케줄러] 비활성화됨")
        return {"enabled": False, "next_run_at": None}


def run_single(keyword_id: Optional[str] = None) -> dict:
    """수동으로 단일 키워드를 발행합니다.

    Args:
        keyword_id: 특정 키워드 ID. None이면 첫 번째 pending 키워드 사용.

    Returns:
        발행 결과 dict
    """
    keyword_entry = _get_next_pending(keyword_id)
    if not keyword_entry:
        if keyword_id:
            return {"success": False, "error": f"ID {keyword_id}에 해당하는 대기 키워드가 없습니다."}
        return {"success": False, "error": "대기 중인 키워드가 없습니다."}

    # 파일 잠금 (수동 실행도 중복 방지)
    lock_fd = _acquire_lock()
    if lock_fd is None:
        return {"success": False, "error": "다른 발행 작업이 진행 중입니다. 잠시 후 다시 시도해주세요."}

    try:
        result = _publish_pipeline(keyword_entry)

        # 설정 업데이트
        config = _load_config()
        config["last_run_at"] = datetime.now(KST).isoformat()
        _save_config(config)

        return result
    finally:
        _release_lock(lock_fd)
