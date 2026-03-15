"""
워드프레스 전체 발행 글 Rank Math 메타 일괄 업데이트 (1회성 스크립트)
content는 건드리지 않고 meta 필드만 PATCH.
"""

import os, re, base64, requests
from dotenv import load_dotenv

load_dotenv()

WP_URL = os.environ.get("WP_URL", "").rstrip("/")
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")

token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
headers = {
    "Authorization": f"Basic {token}",
    "Content-Type": "application/json",
}


def extract_focus_keyword(title: str) -> str:
    """제목에서 핵심 키워드 추출 (괄호/특수문자 제거, 핵심 명사구)."""
    # 괄호 내용 제거
    clean = re.sub(r"[(\[【「][^)\]】」]*[)\]】」]", "", title)
    # 특수문자 제거
    clean = re.sub(r"[^\w가-힣\s]", " ", clean)
    # 연속 공백 정리
    clean = re.sub(r"\s+", " ", clean).strip()
    # 불용어 제거
    stopwords = {"및", "의", "을", "를", "이", "가", "은", "는", "에", "도", "로", "와", "과", "한", "할", "하는", "위한", "대한", "있는", "없는", "모든", "어떤", "이런", "그런"}
    words = [w for w in clean.split() if w not in stopwords and len(w) > 1]
    # 앞에서 3~4단어
    return " ".join(words[:4])


def strip_html(html: str) -> str:
    """HTML 태그 제거."""
    return re.sub(r"<[^>]+>", "", html)


def main():
    # 전체 발행 글 가져오기
    resp = requests.get(
        f"{WP_URL}/wp-json/wp/v2/posts",
        headers=headers,
        params={"per_page": 100, "status": "publish"},
    )
    resp.raise_for_status()
    posts = resp.json()
    print(f"총 {len(posts)}개 글 발견\n")

    for post in posts:
        pid = post["id"]
        title = post["title"]["rendered"]
        content = post["content"]["rendered"]

        focus_keyword = extract_focus_keyword(title)
        meta_description = strip_html(content)[:155].strip()
        rank_math_title = title

        patch_data = {
            "meta": {
                "rank_math_focus_keyword": focus_keyword,
                "rank_math_description": meta_description,
                "rank_math_title": rank_math_title,
            }
        }

        r = requests.post(
            f"{WP_URL}/wp-json/wp/v2/posts/{pid}",
            headers=headers,
            json=patch_data,
        )
        if r.ok:
            print(f"✓ [{pid}] {title}")
            print(f"  keyword: {focus_keyword}")
            print(f"  desc: {meta_description[:60]}...")
            print()
        else:
            print(f"✗ [{pid}] {title} → {r.status_code}: {r.text[:100]}")
            print()

    print("완료!")


if __name__ == "__main__":
    main()
