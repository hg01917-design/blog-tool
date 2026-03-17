#!/bin/bash
# ============================================
#  AutoBlog 자동 배포 크론 스크립트
#  서버 crontab: */5 * * * * bash ~/blog-tool/deploy_cron.sh
# ============================================

DEPLOY_DIR="$HOME/blog-tool"
LOG_FILE="$DEPLOY_DIR/deploy.log"
PID_FILE="$DEPLOY_DIR/gunicorn.pid"

cd "$DEPLOY_DIR" || exit 1

# git pull 실행
PULL_OUTPUT=$(git pull origin main 2>&1)
PULL_EXIT=$?

# 이미 최신이면 종료
if echo "$PULL_OUTPUT" | grep -q "Already up to date"; then
    exit 0
fi

# 변경사항이 있으면 로그 기록 + gunicorn 재시작
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TIMESTAMP] 변경 감지 — git pull 결과:" >> "$LOG_FILE"
echo "$PULL_OUTPUT" >> "$LOG_FILE"

if [ $PULL_EXIT -ne 0 ]; then
    echo "[$TIMESTAMP] git pull 실패 (exit $PULL_EXIT)" >> "$LOG_FILE"
    exit 1
fi

# 의존성 변경 시 설치
if echo "$PULL_OUTPUT" | grep -q "requirements.txt"; then
    echo "[$TIMESTAMP] requirements.txt 변경 — pip install 실행" >> "$LOG_FILE"
    pip3 install -r requirements.txt -q >> "$LOG_FILE" 2>&1
fi

# gunicorn 재시작
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
    kill "$OLD_PID" 2>/dev/null
    sleep 2
fi

export LD_LIBRARY_PATH="$HOME/local-libs/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH"
~/.local/bin/gunicorn \
    --bind 127.0.0.1:5000 \
    --workers 2 \
    --daemon \
    --pid "$PID_FILE" \
    --access-logfile "$DEPLOY_DIR/access.log" \
    --error-logfile "$DEPLOY_DIR/error.log" \
    --timeout 600 \
    app:app

NEW_PID=$(cat "$PID_FILE" 2>/dev/null)
echo "[$TIMESTAMP] gunicorn 재시작 완료 (PID: $NEW_PID)" >> "$LOG_FILE"
echo "---" >> "$LOG_FILE"
