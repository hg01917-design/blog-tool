#!/bin/bash
set -e

echo "========================================"
echo "  Blog Tool 로컬 실행"
echo "========================================"
echo

# 스크립트 디렉토리로 이동
cd "$(dirname "$0")"

# Python 확인
if ! command -v python3 &> /dev/null; then
    echo "[오류] Python3이 설치되어 있지 않습니다."
    echo
    echo "Mac: brew install python3"
    echo "Linux: sudo apt install python3 python3-venv"
    echo
    exit 1
fi

echo "[1/4] Python 확인 완료"
python3 --version

# venv 생성
if [ ! -d "venv" ]; then
    echo
    echo "[2/4] 가상환경 생성 중..."
    python3 -m venv venv
else
    echo "[2/4] 가상환경 이미 존재"
fi

# venv 활성화
source venv/bin/activate

# 패키지 설치
echo
echo "[3/4] 패키지 설치 중..."
pip install -r requirements.txt --quiet
python -m playwright install chromium
echo "[3/4] 패키지 설치 완료"

# .env에 LOCAL_MODE 설정
if [ ! -f ".env" ]; then
    echo "LOCAL_MODE=true" > .env
    echo "[설정] .env 파일 생성 (LOCAL_MODE=true)"
elif ! grep -q "LOCAL_MODE" .env; then
    echo "" >> .env
    echo "LOCAL_MODE=true" >> .env
    echo "[설정] LOCAL_MODE=true 추가"
fi

# 크롬 디버그 모드 실행 (CDP 9222 포트)
echo
echo "[4/5] 크롬 디버그 모드 확인..."
if lsof -i:9222 -sTCP:LISTEN &> /dev/null; then
    echo "[4/5] 크롬이 이미 9222 포트로 실행 중 (스킵)"
else
    echo "[4/5] 크롬을 디버그 모드로 실행 중..."
    CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    CHROME_PROFILE=$(ls ~/Library/Application\ Support/Google/Chrome/ | grep -E "^(Default|Profile [0-9]+)$" | head -1)
    "$CHROME" --remote-debugging-port=9222 --no-first-run --no-default-browser-check --profile-directory="$CHROME_PROFILE" &

    # 포트 열릴 때까지 최대 10초 대기
    CDP_READY=false
    for i in $(seq 1 10); do
        if lsof -i:9222 -sTCP:LISTEN &> /dev/null; then
            CDP_READY=true
            break
        fi
        echo "[4/5] 크롬 시작 대기 중... (${i}/10)"
        sleep 1
    done

    if [ "$CDP_READY" = true ]; then
        echo "[4/5] ✅ Chrome CDP ready (포트 9222)"
    else
        echo "[4/5] ❌ Chrome not running on port 9222"
        echo "크롬이 디버그 모드로 시작되지 않았습니다. 크롬을 수동으로 실행해주세요."
        exit 1
    fi
fi

echo
echo "[5/5] Flask 서버 시작 (localhost:5001)"
echo
echo "========================================"
echo "  브라우저에서 http://localhost:5001 접속"
echo "  종료: Ctrl+C"
echo "========================================"
echo

# Flask 실행
export LOCAL_MODE=true
export FLASK_ENV=development
python app.py
