#!/bin/bash
# 检查本次会话是否修改了关键源文件，如果是则提醒更新对应的 skills
# 被 .claude/settings.json Stop hook 调用

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# 获取本次未提交的修改文件（staged + unstaged）
changed_files=$(git diff --name-only HEAD 2>/dev/null; git diff --name-only --cached 2>/dev/null)
[ -z "$changed_files" ] && exit 0

# 文件 → skill 映射
declare -A file_skill_map
file_skill_map["backend/api/source.py"]="debug-source, modify-source"
file_skill_map["backend/api/sessions.py"]="debug-session, fix-data"
file_skill_map["backend/api/detection.py"]="debug-detection"
file_skill_map["backend/services/detector.py"]="debug-detection"
file_skill_map["backend/api/alarm.py"]="debug-alarm"
file_skill_map["backend/api/cameras.py"]="modify-api"
file_skill_map["backend/api/projects.py"]="modify-api, modify-project-config"
file_skill_map["backend/api/models.py"]="modify-api"
file_skill_map["backend/api/reports.py"]="modify-api"
file_skill_map["backend/api/tasks.py"]="modify-api"
file_skill_map["backend/api/websocket.py"]="modify-api"
file_skill_map["backend/api/channel_manager.py"]="debug-channel"
file_skill_map["backend/models/models.py"]="modify-model"
file_skill_map["backend/main.py"]="debug-electron, build-release"
file_skill_map["backend/core/config.py"]="modify-model"
file_skill_map["backend/hotfix.py"]="debug-source, debug-video"
file_skill_map["backend/hcnetsdk/wrapper.py"]="debug-video"
file_skill_map["electron/main.js"]="debug-electron"
file_skill_map["electron/backend-manager.js"]="debug-electron"
file_skill_map["electron/license-manager.js"]="debug-electron"
file_skill_map["frontend/src/views/Monitor/index.vue"]="debug-frontend, modify-frontend"
file_skill_map["frontend/src/views/Project/index.vue"]="modify-project-config, modify-frontend"
file_skill_map["frontend/src/views/Source/index.vue"]="debug-frontend, modify-frontend"
file_skill_map["frontend/src/views/Data/index.vue"]="debug-frontend, fix-data"
file_skill_map["frontend/src/views/Alarm/index.vue"]="debug-alarm"
file_skill_map["frontend/src/views/Settings/index.vue"]="debug-frontend"
file_skill_map["frontend/src/views/Report/index.vue"]="modify-frontend"
file_skill_map["frontend/src/views/Model/index.vue"]="modify-frontend"
file_skill_map["frontend/src/layout/Navbar.vue"]="debug-frontend, modify-frontend, modify-project-config"
file_skill_map["frontend/src/layout/BottomBar.vue"]="debug-frontend"
file_skill_map["frontend/src/store/useProjectStore.js"]="modify-frontend"
file_skill_map["frontend/src/store/useSourceStore.js"]="modify-frontend"
file_skill_map["frontend/src/store/useSystemStore.js"]="modify-frontend"
file_skill_map["frontend/src/api/detection.js"]="api-sync"
file_skill_map["frontend/src/api/data.js"]="api-sync"
file_skill_map["frontend/src/api/project.js"]="api-sync"
file_skill_map["frontend/src/api/model.js"]="api-sync"
file_skill_map["frontend/src/api/camera.js"]="api-sync"
file_skill_map["frontend/src/api/report.js"]="api-sync"
file_skill_map["frontend/src/router/index.js"]="debug-frontend"
file_skill_map[".github/workflows/build.yml"]="build-release"
file_skill_map["electron/package.json"]="build-release"
file_skill_map["frontend/src/api/index.js"]="api-sync, debug-frontend"
file_skill_map["backend/schemas/camera.py"]="modify-model"
file_skill_map["backend/schemas/model.py"]="modify-model"
file_skill_map["backend/schemas/project.py"]="modify-model"
file_skill_map["backend/schemas/report.py"]="modify-model"
file_skill_map["backend/schemas/task.py"]="modify-model"
file_skill_map["patches/session_fix_patch.py"]="debug-source"

# 收集需要更新的 skills
affected_skills=""
affected_files=""

while IFS= read -r file; do
    [ -z "$file" ] && continue
    if [ -n "${file_skill_map[$file]}" ]; then
        affected_skills="$affected_skills, ${file_skill_map[$file]}"
        affected_files="$affected_files\n  - $file → ${file_skill_map[$file]}"
    fi
done <<< "$changed_files"

# 去重
if [ -n "$affected_skills" ]; then
    unique_skills=$(echo "$affected_skills" | tr ',' '\n' | sed 's/^ *//' | grep -v '^$' | sort -u | paste -sd ',' | sed 's/^,//;s/,$//')
    msg="本次会话修改了关键源文件，以下 skills 可能需要同步更新：\n\n受影响的 skills: $unique_skills\n\n文件映射:$affected_files\n\n请检查这些 skills 的 SKILL.md 是否需要更新（如函数名变更、新增端点、参数变化等）。如果改动较小（如bug修复、不改接口），可以跳过更新。"
    echo "{\"systemMessage\": \"$(echo -e "$msg" | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\n/\\n/g')\"}"
fi
