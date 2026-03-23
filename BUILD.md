# 天军科技AI视觉检测系统 - 构建与发布指南

## 概述

本文档说明如何构建 Windows 安装程序，以及如何通过 GitHub Actions 自动构建并发布到 GitHub / Gitee。

---

## 一、GitHub Secrets（Token 配置）

在 GitHub 仓库 **Settings → Secrets and variables → Actions** 中需要配置以下 2 个 Secret：

| Secret 名称 | 用途 | 获取方式 |
|-------------|------|---------|
| `RELEASE_TOKEN` | GitHub Release 上传（上传到 `17373531860/tianjun-releases` 仓库） | GitHub → Settings → Developer settings → Personal access tokens → 创建 token，权限勾选 `repo`（完整仓库访问） |
| `GITEE_TOKEN` | Gitee Release 上传（上传到 `xu-yanzhi32/tianjun-releases` 和 `xu-yanzhi32/tianjun-releases-2`） | Gitee → 设置 → 私人令牌 → 创建，权限勾选 `projects`。注意 Gitee token 有效期最长 1 年，过期需重新生成并更新 Secret |

### Token 过期检查

如果构建时出现 `401 Unauthorized: Access token is expired`，说明 Gitee Token 已过期，需要：
1. 去 https://gitee.com/profile/personal_access_tokens 生成新 Token
2. 在 GitHub 仓库 Settings → Secrets → 更新 `GITEE_TOKEN` 的值

---

## 二、发布仓库说明

| 仓库 | 平台 | 用途 |
|------|------|------|
| `17373531860/tianjun-ai-vision` | GitHub | 源码仓库（平时设为 private，构建时需临时改为 public 以使用免费 Actions 额度） |
| `17373531860/tianjun-releases` | GitHub | 存放完整安装包（.exe），GitHub Release 有单文件 2GB 限制 |
| `xu-yanzhi32/tianjun-releases` | Gitee | 存放分块安装包 Part 1（前半部分 .part 文件 + merge_installer.bat + checksums.txt） |
| `xu-yanzhi32/tianjun-releases-2` | Gitee | 存放分块安装包 Part 2（后半部分 .part 文件） |

---

## 三、自动构建（GitHub Actions）

### 触发方式

推送 `v*` 格式的 tag 会自动触发完整构建 + 发布流程：

```bash
# 1. 先把仓库改为 public（节省 Actions 费用）
gh repo edit 17373531860/tianjun-ai-vision --visibility public --accept-visibility-change-consequences

# 2. 确保代码已提交并推送
git add -A && git commit -m "v2.0.x: 更新说明" && git push origin main

# 3. 创建并推送 tag
git tag v2.0.8
git push origin v2.0.8

# 4. 等待构建完成后，改回 private
gh repo edit 17373531860/tianjun-ai-vision --visibility private --accept-visibility-change-consequences
```

也可以通过 GitHub Actions 页面手动触发（workflow_dispatch）。

### 构建流程（build.yml）

构建在 `windows-latest` 上运行，超时 300 分钟，流程如下：

1. **Free disk space** — 清理 runner 磁盘空间（删除 .NET、Java 等不需要的工具）
2. **Checkout code** — 拉取源码
3. **Setup Node.js 20** — 安装 Node.js
4. **Setup Miniconda** — 安装 conda，创建 Python 3.10 环境
5. **Install Python dependencies** — `pip install` 安装 PyTorch cu128 及所有后端依赖
6. **Compile Python backend with Nuitka** — 编译 Python 后端为 .pyd（可选加速）
7. **Pack Python environment** — `conda-pack` 打包整个 Python 环境
8. **Download FFmpeg** — 下载 FFmpeg for Windows
9. **Build Frontend** — `npm run build` 构建 Vue.js 前端
10. **Verify resources** — 检查所有资源文件齐全
11. **Build Electron App (dir)** — `electron-builder --dir` 生成解压版应用目录
12. **Install Inno Setup** — 通过 Chocolatey 安装 Inno Setup 6
13. **Build Installer with Inno Setup** — 编译生成 .exe 安装包（无文件大小限制）
14. **Upload Release to GitHub** — 上传完整安装包到 `tianjun-releases`（超过 2GB 会失败但不阻塞后续步骤）
15. **Split installer for Gitee** — 将安装包分割为 ~20 个 ~100MB 的 .part 文件，生成 checksums.txt 和 merge_installer.bat
16. **Upload to Gitee (repo 1)** — 上传前半部分 + 合并脚本 + 校验文件
17. **Upload to Gitee (repo 2)** — 上传后半部分

### 打包方式：Inno Setup

原本使用 electron-builder 内置的 NSIS 打包，但 NSIS 有 2GB 文件大小硬限制（32 位 File 指令），PyTorch cu128 导致安装包超过该限制。现改用 Inno Setup 6：

- **安装脚本**: `electron/build/installer.iss`
- **无文件大小限制**（64 位文件偏移）
- **LZMA2/Ultra64 压缩**
- **自定义安装逻辑**（Pascal 脚本）:
  - 升级前自动备份许可证文件（license.lic、machine_id.txt、hw_verify.txt）
  - 升级前备份数据库（sql_app.db）
  - 安装后恢复许可证文件
  - 自动安装 CH340/CH341 USB 转串口驱动（pnputil 或 SETUP.EXE 回退）
- **安装界面**: 英文（许可协议内容为中文）

### Gitee 分块上传说明

安装包被分成约 20 个 .part 文件（每个 ~100MB），分布在两个 Gitee 仓库：

- **Repo 1** (`tianjun-releases`): part00 ~ part09 + `merge_installer.bat` + `checksums.txt`
- **Repo 2** (`tianjun-releases-2`): part10 ~ part19

用户下载所有文件后运行 `merge_installer.bat`，脚本会：
1. 检查所有 part 文件是否齐全
2. 逐个验证每个 part 的 SHA256 校验值
3. 合并为完整安装包
4. 验证合并后文件的总大小和 SHA256

### 单独触发 Gitee 上传（gitee-upload.yml）

如果构建时 Gitee 上传失败或超时，可以在 GitHub Actions 中手动触发 `Upload to Gitee` 工作流，填入版本号（如 `v2.0.8`），它会从 GitHub Release 下载安装包并重新分块上传到 Gitee。

---

## 四、本地手动构建

### 系统要求

- **操作系统**: Windows 10/11 64位
- **Node.js**: 20.x 或更高版本
- **Python**: Anaconda/Miniconda（conda 环境名: `tianjun`）
- **Inno Setup 6**: https://jrsoftware.org/isdl.php

### 构建步骤

#### 方法一：使用自动构建脚本

```bash
scripts\build-app.bat
```

#### 方法二：手动构建

```bash
# 1. 构建前端
cd frontend
npm install
npm run build

# 2. 打包 Python 环境
conda activate tianjun
pip install conda-pack
conda-pack -n tianjun -o python-env\tianjun-env.tar.gz --force
mkdir python-env\python
cd python-env && tar -xzf tianjun-env.tar.gz -C python && del tianjun-env.tar.gz && cd ..

# 3. 构建 Electron 解压版
cd electron
npm install
npx electron-builder --dir --x64

# 4. 用 Inno Setup 编译安装包
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion="2.0.8" electron\build\installer.iss
# 产物: electron\dist\TianJun-AI-Vision-2.0.8-Setup.exe
```

---

## 五、目录结构

```
项目根目录/
├── .github/workflows/     # CI/CD 工作流
│   ├── build.yml          # 主构建 + 发布流程
│   └── gitee-upload.yml   # 单独的 Gitee 上传流程
├── frontend/              # Vue.js 前端代码
│   └── dist/              # 前端构建产物
├── backend/               # FastAPI 后端代码
│   └── hcnetsdk/lib/      # 海康 SDK DLL 文件（37个）
├── electron/              # Electron 桌面应用
│   ├── main.js            # 主进程
│   ├── preload.js         # 预加载脚本
│   ├── build/             # 构建资源
│   │   ├── icon.ico       # 应用图标
│   │   ├── license.txt    # 许可协议（中文）
│   │   ├── installer.iss  # Inno Setup 安装脚本
│   │   └── installer.nsh  # NSIS 安装脚本（已弃用，保留备份）
│   ├── drivers/CH341SER/  # CH340 USB 驱动（9个文件）
│   └── dist/              # 安装程序输出目录
├── python-env/            # Python 环境
│   └── python/            # conda-pack 解压后的环境
├── ffmpeg/                # FFmpeg 可执行文件
├── scripts/               # 构建脚本
└── BUILD.md               # 本文档
```

---

## 六、版本发布清单

每次发布新版本前，确认以下事项：

- [ ] `electron/package.json` 中的 `version` 字段已更新（如 `"2.0.8"`）
- [ ] 代码已全部提交并推送到 main 分支
- [ ] GitHub Secrets 中的 `RELEASE_TOKEN` 和 `GITEE_TOKEN` 未过期
- [ ] 仓库已改为 public（或有足够的 Actions 余额）
- [ ] Tag 名称与 package.json 版本一致（如 `v2.0.8`）

---

## 七、常见问题

### Q: 构建失败，提示找不到 python-env
A: 请先运行 `scripts\pack-python-env.bat` 打包 Python 环境。

### Q: 安装后无法启动 GPU
A: 请确保已安装 NVIDIA 驱动 560 或更高版本（RTX 5050 需要 570+）。

### Q: GitHub Release 上传失败 (422 / size limit)
A: GitHub Release 单文件限制 2GB。安装包超过此限制时上传会失败，但不影响 Gitee 分块上传。Gitee 上的分块文件可以正常合并使用。

### Q: Gitee 上传超时或失败
A: 在 GitHub Actions 中手动触发 `Upload to Gitee` 工作流，填入版本号即可重新上传。

### Q: Gitee Token 过期 (401 Unauthorized)
A: 去 Gitee 私人令牌页面重新生成，然后在 GitHub Secrets 中更新 `GITEE_TOKEN`。

### Q: 用户合并安装包后报"校验失败"
A: 某个 .part 文件在下载过程中损坏。merge_installer.bat 会指出具体哪个文件校验失败，重新下载该文件即可。

### Q: 应用启动很慢
A: 首次启动需要初始化 Python 环境，可能需要 30-60 秒。后续启动会更快。

---

## 八、技术栈

| 组件 | 技术 | 版本 |
|------|------|------|
| 前端 | Vue.js 3 + Vite + Element Plus | — |
| 后端 | FastAPI + SQLAlchemy + OpenCV | — |
| AI 推理 | YOLO (ultralytics) + PyTorch + CUDA 12.8 | PyTorch 2.10+ |
| 桌面框架 | Electron | 28.x |
| 安装包打包 | Inno Setup 6 | 6.7+ |
| CI/CD | GitHub Actions | — |
| 发布平台 | GitHub Release + Gitee Release（分块） | — |
