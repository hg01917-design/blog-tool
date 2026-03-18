@echo off
chcp 65001 >nul 2>&1
title Blog Tool - Local Mode

echo ========================================
echo   Blog Tool 로컬 실행
echo ========================================
echo.

REM Python 확인
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo.
    echo 다운로드: https://www.python.org/downloads/
    echo 설치 시 "Add Python to PATH" 체크 필수!
    echo.
    pause
    exit /b 1
)

echo [1/4] Python 확인 완료
python --version

REM venv 생성
if not exist "venv" (
    echo.
    echo [2/4] 가상환경 생성 중...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [오류] 가상환경 생성 실패
        pause
        exit /b 1
    )
) else (
    echo [2/4] 가상환경 이미 존재
)

REM venv 활성화
call venv\Scripts\activate.bat

REM 패키지 설치
echo.
echo [3/4] 패키지 설치 중...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [오류] 패키지 설치 실패
    pause
    exit /b 1
)

REM Playwright Chromium 설치
playwright install chromium --with-deps 2>nul
if %errorlevel% neq 0 (
    python -m playwright install chromium
)

echo [3/4] 패키지 설치 완료

REM .env에 LOCAL_MODE 설정
if not exist ".env" (
    echo LOCAL_MODE=true> .env
    echo [설정] .env 파일 생성 (LOCAL_MODE=true)
) else (
    findstr /c:"LOCAL_MODE" .env >nul 2>&1
    if %errorlevel% neq 0 (
        echo.>> .env
        echo LOCAL_MODE=true>> .env
        echo [설정] LOCAL_MODE=true 추가
    )
)

echo.
echo [4/4] Flask 서버 시작 (localhost:5000)
echo.
echo ========================================
echo   브라우저에서 http://localhost:5000 접속
echo   종료: Ctrl+C
echo ========================================
echo.

REM 2초 후 브라우저 자동 열기
start "" "http://localhost:5000"

REM Flask 실행
set LOCAL_MODE=true
set FLASK_ENV=development
python app.py
