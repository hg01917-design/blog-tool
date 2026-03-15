#!/bin/bash
# ============================================
#  AutoBlog 배포 스크립트
#  SSH ControlMaster로 비밀번호 1회만 입력
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── SSH 설정 (~/.ssh/config의 cloudways 호스트 사용) ──
REMOTE="cloudways"
REMOTE_DIR="blog-tool"

# ── 배포 대상 파일 ──
FILES=(
  app.py
  keywords.py
  orders.py
  naver_playwright.py
  requirements.txt
  adsense.html
  templates/index.html
  templates/login.html
  templates/settings.html
  templates/write.html
  templates/keywords.html
  templates/orders.html
)

# ── 색상 ──
G='\033[0;32m'
Y='\033[1;33m'
R='\033[0;31m'
N='\033[0m'

echo -e "${G}========================================${N}"
echo -e "${G}  AutoBlog 배포 시작${N}"
echo -e "${G}========================================${N}"

# ── 1) 마스터 연결 (비밀번호 1회 입력) ──
echo ""
echo -e "${Y}[1/5] SSH 접속 (비밀번호 1회만 입력하세요)...${N}"
ssh -o StrictHostKeyChecking=no -fN $REMOTE 2>/dev/null || true
ssh $REMOTE "echo '  OK: 접속 성공'" || { echo -e "${R}SSH 접속 실패${N}"; exit 1; }

# ── 2) 원격 디렉토리 생성 ──
echo ""
echo -e "${Y}[2/5] 원격 디렉토리 준비...${N}"
ssh $REMOTE "mkdir -p ~/${REMOTE_DIR}/templates ~/${REMOTE_DIR}/data"
echo "  OK"

# ── 3) 파일 업로드 ──
echo ""
echo -e "${Y}[3/5] 파일 업로드 중...${N}"
for f in "${FILES[@]}"; do
  if [ -f "$f" ]; then
    scp "$f" "${REMOTE}:~/${REMOTE_DIR}/${f}"
    echo "  OK $f"
  else
    echo "  -- $f (건너뜀)"
  fi
done

# ── 4) 의존성 설치 + Gunicorn 재시작 ──
echo ""
echo -e "${Y}[4/5] 의존성 설치 + Gunicorn 재시작...${N}"
ssh $REMOTE "cd ~/${REMOTE_DIR} && \
  pip3 install -r requirements.txt -q 2>&1 | tail -3 && \
  python3 -m playwright install chromium 2>&1 | tail -2 && \
  kill \$(cat gunicorn.pid 2>/dev/null) 2>/dev/null || true && \
  sleep 1 && \
  ~/.local/bin/gunicorn \
    --bind 127.0.0.1:5000 \
    --workers 2 \
    --daemon \
    --pid gunicorn.pid \
    --access-logfile access.log \
    --error-logfile error.log \
    --timeout 120 \
    app:app && \
  echo \"  OK Gunicorn PID: \$(cat gunicorn.pid 2>/dev/null)\""

# ── 5) 헬스 체크 ──
echo ""
echo -e "${Y}[5/5] 헬스 체크...${N}"
STATUS=$(ssh $REMOTE "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/login")
if [ "$STATUS" = "200" ]; then
  echo -e "  OK HTTP ${STATUS}"
else
  echo -e "  ${R}HTTP ${STATUS} — 확인 필요${N}"
  ssh $REMOTE "tail -5 ~/${REMOTE_DIR}/error.log 2>/dev/null"
fi

echo ""
echo -e "${G}========================================${N}"
echo -e "${G}  배포 완료!${N}"
echo -e "${G}  https://app.baremi542.com${N}"
echo -e "${G}========================================${N}"
