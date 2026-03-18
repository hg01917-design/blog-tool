# Blog Tool

블로그 자동화 도구 (티스토리, 네이버, 워드프레스)

## 로컬 설치 방법

### 1. 저장소 클론

```bash
git clone https://github.com/hg01917-design/blog-tool
cd blog-tool
```

### 2. 실행

**Windows:**
```
start_local.bat
```

**Mac / Linux:**
```bash
chmod +x start_local.sh
./start_local.sh
```

스크립트가 자동으로 처리하는 것:
- Python 설치 여부 확인
- 가상환경(venv) 생성 + 패키지 설치
- Playwright Chromium 설치
- `.env`에 `LOCAL_MODE=true` 설정
- Flask 서버 실행 (localhost:5000)
- 브라우저에서 자동 열기

### 3. 로컬 모드 동작 방식

`LOCAL_MODE=true`일 때 이미 로그인된 로컬 크롬 프로필을 사용합니다.

- 별도 로그인 불필요 (크롬에서 이미 로그인된 세션 활용)
- 브라우저 창이 보임 (headless=False)
- 크롬 프로필 자동 감지:
  - Windows: `C:\Users\{사용자명}\AppData\Local\Google\Chrome\User Data`
  - Mac: `~/Library/Application Support/Google/Chrome`
  - Linux: `~/.config/google-chrome`

### 4. 필수 환경변수 (.env)

```
LOCAL_MODE=true
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
```
