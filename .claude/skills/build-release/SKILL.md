---
name: build-release
description: "打包发版全流程指南：版本号更新、CI流程、Nuitka编译、conda-pack、Inno Setup安装包、GitHub/Gitee发布。当需要发布新版本或排查构建问题时使用。"
argument-hint: "[版本号或构建问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# build-release: 打包发版流程

你正在帮用户处理天军AI视觉检测系统的打包发版。

需求: $ARGUMENTS

## 版本号位置（发版前需要统一更新）

| 文件 | 位置 | 当前 |
|------|------|------|
| `electron/package.json` | `"version"` | 2.2.1 |
| `electron/splash.html` | 硬编码文本 | v2.2.1 |
| Git tag | `v2.2.1` | 触发CI |

## 自动构建流程 (.github/workflows/build.yml)

**触发条件:** Push `v*` tag 或手动 dispatch

```
1. 环境准备
   ├── Windows runner, Python 3.10, Node.js 20
   ├── 释放磁盘空间 (~20GB)
   └── Setup Miniconda (tianjun env)

2. Python 依赖
   ├── PyTorch cu128 (CUDA 12.8, Blackwell GPU)
   ├── 后端 requirements
   ├── mediapipe (--no-deps, 避免OpenCV冲突)
   ├── opencv-contrib-python <4.12
   └── 验证所有 import

3. Nuitka 编译 (.py → .pyd)
   ├── source.py, detection.py, sessions.py
   ├── projects.py, cameras.py, alarm.py
   ├── reports.py, tasks.py, websocket.py
   ├── models.py, detector.py
   └── 删除原 .py 源码

4. conda-pack → python-env/python/
5. 下载 FFmpeg (win64 GPL)
6. 构建前端 (npm ci && npm run build)
7. electron-builder --dir --x64
8. Inno Setup 6 → TianJun-AI-Vision-{version}-Setup.exe
9. 上传 GitHub Release
10. 分割为 ~100MB chunks → Gitee (2个repo)
```

## 本地手动构建 (scripts/build-app.bat)

```bash
# 1. 构建前端
cd frontend && npm install && npm run build

# 2. 打包Python环境 (如需更新)
cd scripts && pack-python-env.bat
# 验证: python-env/python/python.exe -c "import torch; print(torch.__version__)"

# 3. Electron构建
cd electron && npm install && npm run build:win
```

## 关键构建配置

### Electron Builder (electron/package.json build section)

```json
{
  "extraResources": [
    {"from": "../frontend/dist", "to": "app/dist"},      // Vue前端
    {"from": "../backend/", "to": "backend/"},            // Python后端
    {"from": "../python-env/python", "to": "python/"},    // Python环境
    {"from": "../ffmpeg/", "to": "ffmpeg/"},              // FFmpeg
    {"from": "../drivers/CH341SER", "to": "drivers/CH341SER"}  // 串口驱动
  ]
}
```

### Inno Setup (installer.iss)
- 64位安装包，LZMA2 压缩
- 安装前备份 license.lic，安装后恢复
- 可选安装 CH340 驱动
- 生成约 2GB+ 安装包

### Gitee 分割上传
- 单文件限制 ~100MB → 分割为 ~20 个 chunks
- 跨 2 个 Gitee 仓库（每个约1GB限制）
- merge_installer.bat: SHA256 校验 + 合并

## 常见构建问题

### Python 依赖冲突
- **OpenCV:** mediapipe 依赖 opencv-python，但我们用 opencv-contrib-python
  - 解决: 先删 opencv-python，再装 opencv-contrib-python <4.12，mediapipe 用 --no-deps
- **numpy:** opencv 4.13+ 要求 numpy>=2，与 mediapipe 冲突
  - 解决: 锁定 opencv-contrib-python <4.12
- **protobuf:** mediapipe 需要 protobuf<5
  - 解决: pip install protobuf<5

### Nuitka 编译失败
- 确认 Nuitka 版本兼容 Python 3.10
- C 编译器需要安装 (Windows: MSVC Build Tools)
- 编译后的 .pyd 文件名必须与原 .py 匹配

### conda-pack 问题
- **必须验证 cu128:** `python -c "import torch; print(torch.version.cuda)"`
- **必须验证 sm_120:** Blackwell GPU 支持
- pack 前确保环境干净（无多余包）

### Gitee 上传失败
- Token 过期（一年有效期）→ 重新生成
- 文件太大 → 检查 split 参数
- 可用 gitee-upload.yml 单独重传

## GitHub Secrets 清单

| Secret | 用途 | 有效期 |
|--------|------|--------|
| `RELEASE_TOKEN` | GitHub PAT (repo scope) | 不过期(建议) |
| `GITEE_TOKEN` | Gitee 个人令牌 | 一年 |

## Git 认证配置

GitHub 使用 `gh` CLI 管理认证，token 存储在系统 keyring 中。

```bash
# 检查认证状态
gh auth status

# 如果 token 过期，重新登录（会打开浏览器）
gh auth login -h github.com -p https -w

# 确保 git credential helper 使用 gh（登录后必须执行）
gh auth setup-git

# 确认 remote URL 是干净的（不含嵌入 token）
git remote -v
# 正确: https://github.com/17373531860/tianjun-ai-vision.git
# 错误: https://ghp_xxxxx@github.com/... （旧 token 嵌在 URL 里）
# 修复: git remote set-url origin https://github.com/17373531860/tianjun-ai-vision.git
```

## 发版检查清单

- [ ] 更新版本号 (electron/package.json)
- [ ] 更新 splash.html 版本号
- [ ] 确认代码已合并到 main
- [ ] 检查 GitHub 认证: `gh auth status`，过期则 `gh auth login`
- [ ] 检查 credential helper: `gh auth setup-git`
- [ ] 确认 remote URL 干净（无嵌入 token）
- [ ] 检查 GitHub Secrets 有效 (RELEASE_TOKEN, GITEE_TOKEN)
- [ ] **先设为 public**: `gh repo edit --visibility public --accept-visibility-change-consequences`
- [ ] Push 代码: `git push origin main`
- [ ] Push tag: `git tag v2.x.x && git push origin v2.x.x`
- [ ] 等待 CI 完成 (~45-60分钟): `gh run watch <run_id>`
- [ ] 验证 GitHub Release 和 Gitee 上传
- [ ] **CI 完成后设回 private**: `gh repo edit --visibility private --accept-visibility-change-consequences`
- [ ] 下载安装包测试安装

## 关键文件
- `.github/workflows/build.yml` — 主CI流程 (676行)
- `.github/workflows/gitee-upload.yml` — Gitee重传
- `electron/package.json` — Electron构建配置
- `scripts/build-app.bat` — 本地构建脚本
- `scripts/pack-python-env.bat` — Python环境打包
- `BUILD.md` — 完整构建文档
