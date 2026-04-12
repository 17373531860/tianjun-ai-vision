#!/bin/bash
RUN_ID=$(gh run list --limit 3 --json databaseId,headBranch -q '.[] | select(.headBranch=="v2.4.0") | .databaseId')
echo "Monitoring GitHub Actions Run ID: $RUN_ID"

while true; do
  STATUS=$(gh run view $RUN_ID --json status -q '.status')
  CONCLUSION=$(gh run view $RUN_ID --json conclusion -q '.conclusion')
  echo "$(date '+%H:%M:%S') status=$STATUS conclusion=$CONCLUSION"
  
  if [ "$STATUS" = "completed" ]; then
    break
  fi
  
  # Check every 5 minutes
  sleep 300
done

echo "Setting repo back to Private..."
while ! gh repo edit --visibility private --accept-visibility-change-consequences; do
    echo "Retrying private visibility set..."
    sleep 5
done
echo "Repo is now Private."

if [ "$CONCLUSION" = "success" ]; then
    notify-send "TianJun AI Vision" "v2.4.0 自动打包成功！仓库已设回 Private。" 2>/dev/null || true
else
    notify-send "TianJun AI Vision" "v2.4.0 自动打包失败，请检查日志。仓库已设回 Private。" 2>/dev/null || true
fi

echo "All done."
