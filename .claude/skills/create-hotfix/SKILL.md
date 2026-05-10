---
name: create-hotfix
description: "创建客户端热补丁：hotfix.py编写、Windows bat补丁脚本、CRLF编码、GitHub Release发布、客户端部署。当需要给客户紧急修复而不走完整发版时使用。"
argument-hint: "[要修复的问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__github, mcp__filesystem"
---

# create-hotfix: 客户端热补丁创建

你正在帮用户为天军AI视觉检测系统创建客户端热补丁。

需求: $ARGUMENTS

## 补丁体系

热补丁分两部分：
1. **hotfix.py** — Python 猴子补丁，运行时替换 `VideoSourceManager` 的方法
2. **patch_vX.X.Xa.bat** — Windows 批处理脚本，修复环境问题 + 部署 hotfix.py

## hotfix.py 编写规范

### 结构

```python
"""
热补丁 vX.X.X — 简要描述
部署: 复制到 resources\backend\hotfix.py，重启软件
"""
import os, time, threading, cv2, numpy as np

def apply(app=None):
    """入口：被 main.py 在启动时调用"""
    _patch_xxx()
    if app is not None:
        _patch_yyy(app)
    print("[Hotfix vX.X.X] 补丁已应用")

def _patch_xxx():
    """替换 VideoSourceManager 的某个方法"""
    from backend.api.source import VideoSourceManager
    original = VideoSourceManager.some_method

    def patched_method(self, ...):
        # 修复后的逻辑
        pass

    VideoSourceManager.some_method = patched_method
    print("[Hotfix] some_method 已替换", flush=True)
```

### 要点
- `apply(app)` 是唯一入口，`main.py` 在 FastAPI 启动后调用
- 传 `app` 时可以替换/新增路由（如 `/video_feed`、`/api/v1/debug/channels`）
- 不传 `app` 时只能替换 `VideoSourceManager` 的实例方法
- 所有 print 加 `flush=True`，否则 Nuitka 编译后可能看不到日志
- hotfix.py 位于 `backend/hotfix.py`（开发环境）或 `resources/backend/hotfix.py`（部署环境）

### 已有补丁内容 (v2.0.5)
- `_patch_start_rtsp()` — RTSP H.265 支持 + 3次重试 + 帧验证
- `_patch_start_camera()` — 摄像头 MSMF 后端自动切换 + CAP_ANY/降分辨率回退
- `_patch_video_feed(app)` — `cv2.putText` 崩溃防护
- `_add_debug_route(app)` — `/api/v1/debug/channels` 诊断接口

## Windows bat 补丁脚本规范

### 必须遵守的规则

1. **CRLF 换行符** — Linux 上写完必须转换：
   ```bash
   sed -i 's/\r//g' patch.bat           # 先清除任何残留 \r
   unix2dos patch.bat 2>/dev/null || sed -i 's/$/\r/' patch.bat
   file patch.bat                        # 确认显示 "CRLF line terminators"
   ```
   不转换 = 闪退，这是最常见的坑。

2. **防闪退结构** — 用 `call :main` 包裹：
   ```batch
   @echo off
   call :main
   pause
   goto :eof

   :main
   ... 实际逻辑 ...
   goto :eof
   ```
   即使 `:main` 内部出错，`pause` 一定会执行，窗口不会关闭。

3. **不要用 `chcp 65001`** — 某些 Windows 版本会导致 bat 重新解析出错。所有提示信息用英文/ASCII。

4. **`set /p` 不要放在 `if` 块内** — 提示符中的 `>` 会被当成重定向：
   ```batch
   :: 错误 - > 被当成重定向
   if defined X (
       set /p "VAR=> "
   )

   :: 正确 - 放在 if 外面
   echo Enter path:
   set /p VAR=Path: 
   ```

5. **不要用 `setlocal enabledelayedexpansion`** — 部分 Windows 版本兼容性差，尽量用简单语法。

6. **路径检测 + 手动输入**：
   ```batch
   set "INSTALL_DIR="
   if exist "D:\path1\python.exe" set "INSTALL_DIR=D:\path1"
   if exist "D:\path2\python.exe" set "INSTALL_DIR=D:\path2"

   if defined INSTALL_DIR echo Auto detected: %INSTALL_DIR%
   if not defined INSTALL_DIR echo Not found.

   echo Enter install path (or press Enter to keep):
   set /p INSTALL_DIR=Path: 
   ```

### 补丁脚本标准流程

```
[1] 关闭软件 (taskkill)
[2] 卸载冲突包 (pip uninstall)
[3] 安装正确版本 (pip install --force-reinstall)
[4] 验证 (python -c "...")
[5] 部署 hotfix.py (copy)
```

### 已知客户安装路径
- `D:\TJKJ-LIN\tianjun-ai-vision`
- `D:\tianjun-ai-vision`
- `D:\tianjun\tianjun-ai-vision`
- `D:\tianjunkeji\tianjun-ai-vision`

Python 位于 `%INSTALL_DIR%\resources\python\python.exe`
Backend 位于 `%INSTALL_DIR%\resources\backend\`

## 发布流程

### 1. 创建文件
- `hotfix.py` — 放在 `backend/hotfix.py`
- `patch_vX.X.Xa.bat` — 放在发布目录（如 `~/桌面/v2.2.1/`）

### 2. 转换 CRLF
```bash
sed -i 's/\r//g' patch_vX.X.Xa.bat
unix2dos patch_vX.X.Xa.bat 2>/dev/null || sed -i 's/$/\r/' patch_vX.X.Xa.bat
```

### 3. 上传到 GitHub Release
```bash
gh release upload vX.X.X patch_vX.X.Xa.bat hotfix.py --clobber
```

### 4. 更新 Release Notes
在 Release 说明中添加补丁安装步骤。

### 5. 客户操作
客户下载 `patch_vX.X.Xa.bat` + `hotfix.py` → 放同一目录 → 双击 bat → 重启软件。

## 常见问题

### OpenCV/NumPy ABI 不兼容
- 症状: `cv2.putText`/`cv2.resize` 报 `img is not a numpy array`
- 原因: `opencv-contrib-python>=4.11` 与 `numpy<2.0` ABI 不兼容
- 修复: 卸载所有 opencv → `pip install --force-reinstall --no-deps opencv-contrib-python==4.10.0.84` + `pip install --force-reinstall numpy==1.26.4`
- 关键: 必须 `--force-reinstall` 两个包，仅 `pip install` 不会重装已有版本

### 摄像头 MSMF 后端竞争
- 症状: benchmark 显示 MSMF 30fps 但实际 `can't grab frame`
- 原因: 测试 MSMF 前没释放 DirectShow
- 修复: `release()` + `sleep(0.3)` 后再打开 MSMF

### bat 脚本闪退
1. 检查 CRLF（`file xxx.bat` 应显示 `CRLF line terminators`）
2. 检查是否用了 `chcp 65001`
3. 检查 `set /p` 是否在 `if` 块内
4. 确认使用了 `call :main` + `pause` 结构

## changelog 记录
补丁发布后更新 `docs/changelog/` 和 `docs/CHANGELOG.md`，类型用 `hotfix`。
