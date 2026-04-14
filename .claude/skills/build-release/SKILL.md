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

### Python 依赖冲突 (经客户实测确认的安全版本)

**锁定版本（CI 和客户端修复均验证通过）：**
```
numpy==1.26.4
opencv-contrib-python==4.10.0.84
mediapipe==0.10.20  (必须 --no-deps)
protobuf<5
```

- **OpenCV:** mediapipe 依赖 opencv-python，但我们用 opencv-contrib-python
  - 解决: 先删 opencv-python，再装 opencv-contrib-python==4.10.0.84，mediapipe 用 --no-deps
  - **不能有 opencv-python 和 opencv-contrib-python 共存**，否则 import 冲突
- **numpy:** opencv 4.11+ 编译时使用 numpy 2.x ABI，与 numpy 1.x 运行时不兼容
  - 症状: `cv2.imencode` 报 `img is not a numpy array`，`ufunc 'isnan' not supported`
  - 解决: 锁定 numpy==1.26.4 + opencv-contrib-python==4.10.0.84
- **mediapipe:** 必须 --no-deps 安装，否则会拉入 opencv-python 覆盖 opencv-contrib-python
  - 安装顺序: numpy → opencv-contrib-python → mediapipe(--no-deps) → protobuf<5
- **protobuf:** mediapipe 需要 protobuf<5
  - 解决: pip install "protobuf<5"

### conda numpy ABI 陷阱 (重要!)
- **症状:** 客户端 `cv2.putText`/`cv2.resize`/`cv2.imencode` 报 `img is not a numpy array`
- **根因:** conda 环境的 numpy 是 MKL 编译的，pip 的 opencv 是 OpenBLAS 编译的，C 层 ABI 不兼容
- **CI 必须:** `pip install --force-reinstall numpy==1.26.4`，不加 `--force-reinstall` pip 会跳过同版本
- **客户端修复:** 必须物理删除 `site-packages/numpy/` + `numpy.libs/` 目录后重装，单纯 pip uninstall/install 不够
- **安装顺序:** 先装 numpy==1.26.4（pip），再装 opencv-contrib-python==4.10.0.84（pip），确保同源
- **已有修复脚本:** `patch_numpy_opencv_fix.bat`（使用清华镜像，支持离线 whl）

### TensorRT 中文路径陷阱
- **症状:** 模型格式转换报 `[TRT] [E] Input file cannot be found`，但文件实际存在
- **根因:** TensorRT 的 C 库在 Windows 上使用 ANSI 编码打开文件，无法处理中文字符路径
- **影响范围:** 模型文件名、模型所在目录名、整个路径中任何包含中文的部分
- **解决:** 模型文件名只用英文字母、数字、下划线、连字符
- **TODO:** 可在代码层面加自动检测+临时复制逻辑（检测到非 ASCII 路径时复制到临时英文路径再转换）

### batch 脚本编码规范
- **Windows .bat 文件中不能包含任何非 ASCII 字符**（中文、框线字符 ─ 等都不行）
- `chcp 65001` 只改控制台输出编码，不改 cmd.exe 解析文件的编码
- UTF-8 中文字节被 GBK 拆解后，含 `)` `|` `"` 等字符会破坏批处理语法
- 非 ASCII 输出只能放在 `powershell -Command "..."` 块内
- CI 生成的 merge_installer.bat、客户端补丁脚本都必须遵守此规范

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

| Secret | 用途 | 有效期 | 上次更新 |
|--------|------|--------|----------|
| `RELEASE_TOKEN` | GitHub Fine-grained PAT → `tianjun-releases` repo (Contents: RW) | 90天 | 2026-04-03 |
| `GITEE_TOKEN` | Gitee 个人令牌 | 一年 | — |

### RELEASE_TOKEN 过期续期流程

CI 上传 Release 到 `17373531860/tianjun-releases` 时报 HTTP 401 = Token 过期。

1. 登录 GitHub 账号 `17373531860`
2. 打开 https://github.com/settings/tokens?type=beta (Fine-grained tokens)
3. 点 **Generate new token**:
   - Name: `release-upload`
   - Expiration: 90天
   - Repository access: **Only select repositories** → `tianjun-releases`
   - Permissions → Contents: **Read and write**
4. 复制生成的 token（`github_pat_` 开头）
5. 设置到 CI:
   ```bash
   gh secret set RELEASE_TOKEN --body "<新token>" --repo 17373531860/tianjun-ai-vision
   ```
   或者用户给 token → 助手执行上述命令

**注意: Token 值绝不能写入代码/skill/任何 git 追踪的文件。只存放在 GitHub Secrets 中。**

## Git 认证配置

GitHub 使用 `gh` CLI 管理认证，token 存储在系统 keyring 中。

### Token 过期处理流程

当 `git push` 报错 `Invalid username or token` 或 `refusing to allow an OAuth App` 时：

```bash
# 第1步: 检查认证状态，确认是否过期
gh auth status

# 第2步: 重新登录（会打开浏览器，在 GitHub Mobile app 确认）
# 重要: 必须加 -s workflow scope，否则无法推送 .github/workflows/ 下的文件
gh auth login -h github.com -p https -w -s workflow
# 输出会给出一个 URL 和一次性验证码
# → 浏览器打开 https://github.com/login/device
# → 输入验证码
# → GitHub Mobile app 弹出确认，点确认

# 第3步: 配置 credential helper（每次 login 后必须执行）
gh auth setup-git

# 第4步: 确认 remote URL 是干净的（不含嵌入 token）
git remote -v
# 正确: https://github.com/17373531860/tianjun-ai-vision.git
# 错误: https://ghp_xxxxx@github.com/... （旧 token 嵌在 URL 里）
# 修复: git remote set-url origin https://github.com/17373531860/tianjun-ai-vision.git

# 第5步: 验证推送
git push origin main
```

### 常见报错对照

| 报错信息 | 原因 | 解决 |
|----------|------|------|
| `Invalid username or token` | token 过期 | 重新 `gh auth login` |
| `refusing to allow an OAuth App to create or update workflow` | token 缺少 `workflow` scope | 重新 `gh auth login -s workflow` |
| `remote: Repository not found` | repo 是 private 且 token 无权限 | 检查 `gh auth status` 里的 scopes |
| `fatal: 鉴权失败` | remote URL 嵌了旧 token | `git remote set-url origin` 清理 |

## 发版检查清单

- [ ] 更新版本号 (electron/package.json)
- [ ] 更新 splash.html 版本号
- [ ] 确认代码已合并到 main
- [ ] 验证依赖版本: numpy==1.26.4, opencv-contrib-python==4.10.0.84, mediapipe==0.10.20, protobuf<5
- [ ] 确认无 opencv-python 包（只有 opencv-contrib-python）
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
