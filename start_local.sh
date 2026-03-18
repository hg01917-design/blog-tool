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

echo
echo "[4/4] Flask 서버 시작 (localhost:5000)"
echo
echo "========================================"
echo "  브라우저에서 http://localhost:5000 접속"
echo "  종료: Ctrl+C"
echo "========================================"
echo

# 브라우저 자동 열기 (백그라운드)
if [[ "$OSTYPE" == "darwin"* ]]; then
    open "http://localhost:5000" &
elif command -v xdg-open &> /dev/null; then
    xdg-open "http://localhost:5000" &
fi

# Flask 실행
export LOCAL_MODE=true
export FLASK_ENV=development
python app.py
