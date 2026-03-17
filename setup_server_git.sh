#!/bin/bash
# ============================================
#  서버 초기 설정: git clone + crontab 등록
#  로컬에서 실행: bash setup_server_git.sh
# ============================================

REMOTE="blog-server"
REPO="https://github.com/hg01917-design/blog-tool.git"
REMOTE_DIR="blog-tool"

echo "=== 1) 서버에 git repo 초기화 ==="
ssh $REMOTE "
  cd ~/$REMOTE_DIR || exit 1

  # 이미 git repo면 remote만 확인
  if [ -d .git ]; then
    echo 'git repo 이미 존재'
    git remote -v
  else
    # 기존 파일 백업 후 git clone
    echo 'git init + remote 설정'
    git init
    git remote add origin $REPO 2>/dev/null || git remote set-url origin $REPO
    git fetch origin main
    git reset --hard origin/main
  fi

  echo ''
  echo 'git 상태:'
  git status
"

echo ""
echo "=== 2) deploy_cron.sh 권한 설정 ==="
ssh $REMOTE "chmod +x ~/$REMOTE_DIR/deploy_cron.sh"

echo ""
echo "=== 3) crontab 등록 (5분마다) ==="
ssh $REMOTE "
  CRON_CMD='*/5 * * * * bash ~/blog-tool/deploy_cron.sh'
  # 중복 방지: 기존 항목 제거 후 추가
  (crontab -l 2>/dev/null | grep -v 'deploy_cron.sh'; echo \"\$CRON_CMD\") | crontab -
  echo 'crontab 등록 완료:'
  crontab -l | grep deploy_cron
"

echo ""
echo "=== 4) DEPLOY_TOKEN 설정 ==="
ssh $REMOTE "
  cd ~/$REMOTE_DIR
  if grep -q DEPLOY_TOKEN .env 2>/dev/null; then
    echo 'DEPLOY_TOKEN 이미 설정됨'
  else
    TOKEN=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    echo \"DEPLOY_TOKEN=\$TOKEN\" >> .env
    echo \"DEPLOY_TOKEN 생성: \$TOKEN\"
    echo '→ 로컬에서 배포 호출 시 이 토큰 사용'
  fi
"

echo ""
echo "=== 완료 ==="
echo "이제 로컬에서 git push만 하면 5분 안에 서버에 자동 반영됩니다."
echo "즉시 배포: curl -X POST https://app.baremi542.com/api/deploy -H 'Authorization: Bearer {TOKEN}'"
