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

---

## 版本号的单一数据源 (v2.7.12+)

**唯一权威来源: `electron/package.json` 的 `.version` 字段**。

其他所有地方都必须和它保持一致:

| 文件/位置 | 用途 | 约束 |
|-----------|------|------|
| `electron/package.json` `.version` | **权威源**, CI 从这里读 | 改版本只改这里 + splash |
| `electron/splash.html` `.version` div | 启动画面显示 | 同步 package.json |
| Git tag `vX.Y.Z` | 触发 Release 流程 + 一致性校验 | tag 的 X.Y.Z 必须 == package.json |
| `docs/changelog/vX.Y.Z_*.md/json` | 变更记录 | 文件名版本号一致 |
| `docs/CHANGELOG.md` 顶部 | 汇总 | 手动维护 |

CI 的 "Build Installer with Inno Setup" 步骤强制做 **tag ↔ package.json 一致性校验**:
```powershell
$pkg = Get-Content "electron/package.json" -Raw | ConvertFrom-Json
$version = $pkg.version
if ($refName -like "v*" -and $refName.TrimStart("v") -ne $version) {
  echo "ERROR: tag 与 package.json 不一致"; exit 1
}
```
tag 不一致直接中止构建, 防止"tag=v2.7.11 但打出来的包还叫 v2.0.8"这种惨剧.

### 历史踩过的坑 (2026-04-22, v2.0.8 → v2.7.11)

**症状**: 副机下载 Action Artifact 的安装包, 解压出来文件名永远是 `TianJun-AI-Vision-2.0.8-Setup.exe`, 和 tag (v2.1 ~ v2.7.11) 都对不上.

**根因**: 旧 build.yml 版本号来源是 `github.ref_name`:
```powershell
$version = "${{ github.ref_name }}".TrimStart("v")
if (-not $version -or $version -eq "${{ github.ref_name }}") { $version = "2.0.8" }  # ← 惨案
```
- push tag `v2.7.11` 时 `ref_name=v2.7.11`, TrimStart 得到 `2.7.11`, 正确;
- push main 时 `ref_name=main`, TrimStart 不去任何字符, fallback 写死 `2.0.8`.

半年里 main 的 Artifact 构建一直被当"最新包"下载, 版本号全错. 这个问题直到客户副机装包才暴露.

**修复**: 版本号从 `electron/package.json` 读, tag 只做一致性校验, 不参与版本号提取.

---

## 两种安装包上传通道 (v2.7.12+ 新规范)

项目维护两条并行的下载通道, **不要再搞双通道都分卷这种蠢事**:

### 通道 A — Action Artifact (内部/团队)
- **不分包**, 上传完整 `TianJun-AI-Vision-X.Y.Z-Setup.exe` (约 3.75 GB)
- **所有 push 都跑** (main / tag / PR)
- 用途: 内部测试、开发机下载、CI 调试
- 配额: GitHub 给付费账号 (Pro) 的公开 repo 有限 artifact 配额, 满了就 fail, 此时用 `continue-on-error: true` 不阻塞 Release 通道
- 保留 30 天

### 通道 B — GitHub Release 分卷 (外部/副机)
- **分卷**, 拆成 1.9 GB 一块的 `*.part00.part / part01.part / ...` + `checksums.txt` + `merge_installer.bat`
- **只在 push tag `v*` 时跑**
- 上传到独立 public repo: `17373531860/tianjun-releases`
- 用途: 副机/客户下载装机
- 原因: GitHub Release 单文件硬限 **2 GB**, 3.75 GB 的 Setup.exe 必须切;  CI 生成合并脚本给用户用

### 为什么 A 不分卷
1. Action Artifact 没有单文件 2 GB 限制 (实测 4 GB+ 可传)
2. 下载时自动 zip 压成一个文件, 用户解压直接拿到完整 exe, 不用跑合并脚本
3. 开发/团队内部用, 少一步合并能少一个出错点

### 错误示范 (v2.7.11 犯过的)
把 Action 也改成上传分卷:
- 多此一举: 内部下载没 2GB 限制, 为什么要切?
- 用户双击 merge_installer.bat 才能装, 多一步出错可能
- 分卷文件的文件名又把 v2.0.8 bug 暴露了: 用户从 Artifact 下的分卷里 part00 的前缀永远是 `TianJun-AI-Vision-2.0.8-Setup.exe.part00.part`
**结论: Action Artifact 永远直传完整 exe, 只有 Release 需要分卷.**

---

## merge_installer.bat 的踩坑全记录

历次发版这个小小的 bat 翻车 N 次, 把所有坑都记在这, **以后改 build.yml 里生成 bat 的 PowerShell 之前先读这里**.

### 坑 1: BOM 导致 @echo off 第一行失效
```powershell
$content | Out-File -FilePath "merge_installer.bat" -Encoding UTF8   # ← 错误
```
Windows PowerShell 5.1 的 `-Encoding UTF8` 默认**带 BOM** (`EF BB BF`), cmd 把 BOM 当第一行的前 3 字节, `@echo off` 匹配失败, 命令全部回显 → 所有变量/goto 提前暴露 → 脚本乱成一团 → 秒退无提示.

**正确做法**: 用 `[IO.File]::WriteAllBytes` + `[Text.Encoding]::ASCII.GetBytes`.

### 坑 2: 嵌 PowerShell 单行命令
```bat
powershell -ExecutionPolicy Bypass -Command "& { $foo = 'bar'; ... }"
```
在 PowerShell here-string 里生成 bat, here-string 里的 `$` 要写成 `` `$ `` 转义, 单引号双引号互相打架, 一个生成错误整个 bat 炸掉. 而且嵌套的 `"..."` 在 cmd 层被意外闭合, PowerShell 层执行到一半就挂.

**正确做法**: bat 里只用 cmd 内置命令 — `copy /b`, `certutil`, `findstr`, `if`, `for`, `goto`, `set`. 够用.

### 坑 3: 硬编码版本号让 bat 不通用
```bat
set "F0=TianJun-AI-Vision-2.7.11-Setup.exe.part00.part"
if not exist "%F0%" goto MISSING     # ← 用户手头是 v2.0.8 分卷, 永远 MISSING
```
每次发版重新生成一份 bat 又硬编码 version, 用户把 v2.0.8 的分卷和 v2.7.11 的 bat 放一起就炸.

**正确做法**: 用 `dir /b *.part00.part` 动态探测第一个分卷文件名, `set "OUT=%P0:.part00.part=%"` 子字符串替换得到 exe 名. bat 跨版本通用.

### 坑 4: call :Label + setlocal EnableDelayedExpansion 参数展开诡异
```bat
setlocal EnableDelayedExpansion
call :VerifyPart "TianJun...part00.part" "HASH"
...
:VerifyPart
set "FNAME=%~1"
if not exist "%FNAME%" ( echo [MISSING] ... )   # ← 有时判定错, 实际文件存在也报 MISSING
```
历史上踩过, 在延迟展开模式下 `call :Label` 带参数某些场景 `%~1` 会被二次展开破坏.

**正确做法**: 二选一, 不要两个一起用:
- 用 `call :Label` 子程序 → 不要 `setlocal EnableDelayedExpansion`, 子程序内只用 `%1 %2`
- 用 `setlocal EnableDelayedExpansion` → 不要 `call :Label` 带参数, 所有累积写在 for 循环里用 `!VAR!`

### 坑 5: echo 文本含 `(` `)` `!` `|` `&` 被 cmd 提前解析
```bat
if %BAD% GTR 0 (
  echo %BAD% part file(s) corrupted. Re-download from:
)
```
`file(s)` 的 `)` 被 cmd 当作外层 `if (...)` 的闭合, 后面 `corrupted. Re-download from:` 被当成独立命令, 报 `此时不应有 corrupted.。`

**正确做法**: 所有 echo 文本只用 ASCII 字母/数字/`-` `_` `:` `.` `[` `]`, 不含 `( ) ! | & < > ^` 等 cmd 特殊字符; 确实需要时用 `^(` `^)` 转义.

### 坑 6: 双击 bat 时 cwd 不一定是 bat 所在目录
某些 Windows 环境 (或者通过快捷方式触发) 双击 bat 时 `%cd%` 是 `C:\Windows\System32` 或别的, `for %%f in (*.part)` 在错误目录里找不到文件.

**正确做法**: bat 第一行永远加 `cd /d "%~dp0"`, 强制切到 bat 所在目录. 同时打印 `echo Working folder: %cd%` 方便排查.

### 坑 7: certutil 输出 parse 陷阱
```bat
for /f "skip=1 tokens=*" %%h in ('certutil -hashfile X SHA256') do ...
```
- 中文系统 certutil 第一行/第三行是中文, `skip=1` 不够
- 某些版本 certutil 输出 hash 里混空格
- 对中文系统的 codepage, `tokens=*` 的分隔符也会变

**正确做法**: 不 parse, 用 `findstr /i /c:"HASH"` 匹配:
```bat
certutil -hashfile "%FILE%" SHA256 | findstr /i /c:"%EXPECTED_HASH%" >nul
if errorlevel 1 (...hash mismatch...) else (...ok...)
```
hash 是 64 位随机 hex, 在 certutil 输出里作为子串碰撞概率 ~0.

### 坑 8: Windows Defender / MoTW 拦截
- 用户浏览器下的 bat 带 Mark of the Web ADS, SmartScreen 可能拦
- 某些杀毒软件对 "大文件合并 + hash 校验" 组合行为判毒
- 表现: 双击 bat 看似秒退, 实际是被杀软中断

**正确排查**: 让用户在 PowerShell 里 `.\merge_installer.bat` 跑, 能看到 cmd 错误输出. 如果也秒退, 让用户右键 bat → 属性 → 底部 "解除锁定" 复选框打勾.

### 当前 bat 模板 (build.yml 里生成的)
- 纯 ASCII + CRLF + 无 BOM (`[IO.File]::WriteAllBytes` + ASCII encoding)
- `cd /d "%~dp0"` 锁定目录
- `dir /b *.part00.part` 动态探测输出 exe 名
- `findstr /i /c:"HASH"` 做 SHA256 校验 (best-effort, 对不上只警告继续)
- `call :Sub` 子程序; **不**用 `setlocal EnableDelayedExpansion`
- 所有错误路径都 pause + exit /b, 绝不闪退

---

## 自动构建流程 (.github/workflows/build.yml)

### 触发条件

| 事件 | Artifact (完整 exe) | Release 分卷 |
|------|---------------------|--------------|
| `push` main | ✅ | ❌ |
| `push` PR | ✅ | ❌ |
| `push` tag `v*` | ✅ | ✅ |
| `workflow_dispatch` 手动 | ✅ | ❌ |

### 主流程

```
1. 环境准备 (Windows runner, Python 3.10 + conda, Node.js 20)
2. Python 依赖 (PyTorch cu128 + numpy 1.26.4 + opencv 4.10 + mediapipe --no-deps)
3. Nuitka 编译 (source/detection/sessions/projects/cameras/alarm/reports/tasks/websocket/models/detector → .pyd)
4. conda-pack → python-env/python/
5. 下载 FFmpeg (win64 GPL)
6. 前端构建 (npm ci && npm run build)
7. electron-builder --dir --x64
8. Inno Setup 6 → TianJun-AI-Vision-{package.json.version}-Setup.exe
9. [通道 A] 上传完整 exe 为 Action Artifact (所有 push 都跑)
10. [通道 B, 仅 tag] Split 为 1.9GB 分卷 + 生成 checksums.txt + merge_installer.bat
11. [通道 B, 仅 tag] 上传到 17373531860/tianjun-releases Release
```

---

## 关键构建配置

### Electron Builder (electron/package.json build section)

```json
{
  "extraResources": [
    {"from": "../frontend/dist", "to": "app/dist"},
    {"from": "../backend/", "to": "backend/"},
    {"from": "../python-env/python", "to": "python/"},
    {"from": "../ffmpeg/", "to": "ffmpeg/"},
    {"from": "../drivers/CH341SER", "to": "drivers/CH341SER"}
  ]
}
```

### Inno Setup (installer.iss)
- 64位, LZMA2 压缩, 约 3.75 GB
- 安装前备份 license.lic, 安装后恢复
- 可选安装 CH340 驱动
- `OutputBaseFilename=TianJun-AI-Vision-{#MyAppVersion}-Setup`
- CI 传入 `/DMyAppVersion="$version"`, $version 从 `electron/package.json` 读

---

## 常见构建问题

### Python 依赖冲突 (经客户实测的安全版本)

**锁定版本 (CI 和客户端修复均验证通过)**:
```
numpy==1.26.4
opencv-contrib-python==4.10.0.84
mediapipe==0.10.20  (必须 --no-deps)
protobuf<5
```

- **OpenCV**: mediapipe 依赖 opencv-python, 但我们用 opencv-contrib-python
  - 解决: 先删 opencv-python, 再装 opencv-contrib-python==4.10.0.84, mediapipe 用 --no-deps
  - **不能有 opencv-python 和 opencv-contrib-python 共存**, 否则 import 冲突
- **numpy**: opencv 4.11+ 编译时用 numpy 2.x ABI, 与 numpy 1.x 运行时不兼容
  - 症状: `cv2.imencode` 报 `img is not a numpy array`, `ufunc 'isnan' not supported`
  - 解决: 锁定 numpy==1.26.4 + opencv-contrib-python==4.10.0.84
- **mediapipe**: 必须 --no-deps, 否则拉入 opencv-python 覆盖 opencv-contrib-python
  - 安装顺序: numpy → opencv-contrib-python → mediapipe(--no-deps) → protobuf<5
- **protobuf**: mediapipe 需要 protobuf<5

### conda numpy ABI 陷阱 (重要!)
- **症状**: 客户端 `cv2.putText`/`cv2.resize`/`cv2.imencode` 报 `img is not a numpy array`
- **根因**: conda 环境 numpy 是 MKL 编译, pip 的 opencv 是 OpenBLAS 编译, C 层 ABI 不兼容
- **CI**: `pip install --force-reinstall numpy==1.26.4`, 不加 `--force-reinstall` pip 会跳过同版本
- **客户端修复**: 必须物理删除 `site-packages/numpy/` + `numpy.libs/` 目录后重装
- **安装顺序**: 先装 numpy==1.26.4 (pip), 再装 opencv-contrib-python==4.10.0.84 (pip)
- **修复脚本**: `patch_numpy_opencv_fix.bat` (清华镜像, 支持离线 whl)

### TensorRT 中文路径陷阱
- **症状**: 模型格式转换报 `[TRT] [E] Input file cannot be found`, 但文件实际存在
- **根因**: TensorRT C 库在 Windows 上用 ANSI 编码打开文件, 无法处理中文字符路径
- **影响**: 模型文件名、所在目录名、路径中任何包含中文的部分
- **解决**: 模型文件名只用英文字母、数字、下划线、连字符

### batch 脚本编码规范 (见上方 merge_installer.bat 坑 1/5)
- `.bat` 文件**不能含任何非 ASCII 字符** (中文/框线字符 ─ 等都不行)
- `chcp 65001` 只改控制台输出编码, 不改 cmd.exe 解析文件的编码
- UTF-8 中文字节被 GBK 拆解后, 含 `)` `|` `"` 等字符会破坏批处理语法
- 非 ASCII 输出只能在 `powershell -Command "..."` 块内 (但该坑 2 不推荐嵌 PS, 最好彻底避免非 ASCII)

### Nuitka 编译失败
- 确认 Nuitka 版本兼容 Python 3.10
- C 编译器必须安装 (Windows: MSVC Build Tools)
- 编译后 .pyd 文件名必须与原 .py 匹配

### conda-pack 问题
- **必须验证 cu128**: `python -c "import torch; print(torch.version.cuda)"`
- **必须验证 sm_120**: Blackwell GPU 支持
- pack 前确保环境干净 (无多余包)

---

## GitHub Secrets 清单

| Secret | 用途 | 有效期 | 上次更新 |
|--------|------|--------|----------|
| `RELEASE_TOKEN` | GitHub Fine-grained PAT → `tianjun-releases` repo (Contents: RW) | 90天 | 2026-04-03 |

### RELEASE_TOKEN 过期续期流程

CI 上传 Release 到 `17373531860/tianjun-releases` 时报 HTTP 401 = Token 过期.

1. 登录 GitHub 账号 `17373531860`
2. 打开 https://github.com/settings/tokens?type=beta (Fine-grained tokens)
3. 点 **Generate new token**:
   - Name: `release-upload`
   - Expiration: 90天
   - Repository access: **Only select repositories** → `tianjun-releases`
   - Permissions → Contents: **Read and write**
4. 复制生成的 token (`github_pat_` 开头)
5. 设置到 CI:
   ```bash
   gh secret set RELEASE_TOKEN --body "<新token>" --repo 17373531860/tianjun-ai-vision
   ```

**Token 值绝不能写入代码/skill/任何 git 追踪的文件. 只存放在 GitHub Secrets 中.**

---

## Git 认证配置

GitHub 使用 `gh` CLI 管理认证, token 存储在系统 keyring 中.

### Token 过期处理流程

```bash
# 第1步: 检查认证状态
gh auth status

# 第2步: 重新登录 (必须加 -s workflow, 否则推不了 .github/workflows/*)
gh auth login -h github.com -p https -w -s workflow

# 第3步: 配置 credential helper
gh auth setup-git

# 第4步: 确认 remote URL 干净 (不含嵌入 token)
git remote -v

# 第5步: 验证
git push origin main
```

### 常见报错

| 报错 | 原因 | 解决 |
|------|------|------|
| `Invalid username or token` | token 过期 | 重新 `gh auth login` |
| `refusing to allow an OAuth App to create or update workflow` | 缺 `workflow` scope | 加 `-s workflow` 重新登录 |
| `remote: Repository not found` | private repo 权限不足 | 检查 `gh auth status` scopes |
| `fatal: 鉴权失败` | remote URL 嵌了旧 token | `git remote set-url origin` 清理 |

---

## 发版检查清单

**强烈建议按顺序跑, 每一项打勾再下一项.**

### 代码阶段
- [ ] 确认所有改动已合并到 main
- [ ] 更新 `electron/package.json` 的 `.version` (**唯一权威源**)
- [ ] 更新 `electron/splash.html` 里的 `v<版本>`
- [ ] 写 `docs/changelog/vX.Y.Z_日期.md` + `.json`
- [ ] 更新 `docs/CHANGELOG.md` 顶部
- [ ] 相关 skill 更新 (debug-xxx / 本 skill 本身)
- [ ] 依赖版本没变: numpy==1.26.4, opencv-contrib-python==4.10.0.84, mediapipe==0.10.20, protobuf<5
- [ ] 确认无 opencv-python (只有 opencv-contrib-python)

### 认证阶段
- [ ] `gh auth status`, 过期则 `gh auth login -s workflow`
- [ ] `gh auth setup-git`
- [ ] `git remote -v` 确认 URL 干净
- [ ] GitHub Secrets `RELEASE_TOKEN` 未过期 (不到 90 天)

### 推送阶段
- [ ] repo 临时设 public (CI 才有流量): `gh repo edit --visibility public --accept-visibility-change-consequences`
- [ ] 选择性 `git add` (不要把 .pcapng / test_*.py 等调试文件提进去)
- [ ] `git commit -m "release: vX.Y.Z - ..."`
- [ ] `git push origin main`
- [ ] `git tag -a vX.Y.Z -m "vX.Y.Z: ..."` (tag 版本号必须和 package.json 完全一致)
- [ ] `git push origin vX.Y.Z`

### CI 阶段 (~45-60 min)
- [ ] `gh run list --limit 3` 确认 CI 触发 (push main + push tag 两条 run)
- [ ] `gh run view <id>` 看步骤状态, Inno Setup 步会校验 tag ↔ package.json 一致性
- [ ] 全绿后验证两条通道:
  - Artifact: `gh api .../actions/runs/<id>/artifacts`, 应有 `TianJun-AI-Vision-Installer`, 体积 ~3.75GB
  - Release: `gh release view vX.Y.Z --repo 17373531860/tianjun-releases`, 应有 N 个 .part + checksums.txt + merge_installer.bat

### 收尾
- [ ] repo 改回 private: `gh repo edit --visibility private --accept-visibility-change-consequences`
- [ ] 内部/开发机: 下 Action Artifact 完整 exe 直接安装
- [ ] 副机/客户: 给 Release 页面链接, 让他们下全部分卷 + merge_installer.bat, 双击运行

### 装机验证 (本次 v2.7.11 的教训 → 强烈建议加)
- [ ] 下完装后**先确认 exe 文件名里的版本号和 tag 一致**(例如 v2.7.11 的 tag 打出来的包必须是 `TianJun-AI-Vision-2.7.11-Setup.exe`, 不是 2.0.8). 不一致立刻回查 build.yml 版本号来源是不是又走错.
- [ ] 安装后检查 "帮助 → 关于" 版本号显示正确
- [ ] 启动画面 splash 显示正确版本号

---

## 关键文件

- `.github/workflows/build.yml` — 主 CI 流程 (~580 行)
- `electron/package.json` — **版本号唯一权威源** + Electron 构建配置
- `electron/splash.html` — 启动画面, 版本号需和 package.json 同步
- `electron/build/installer.iss` — Inno Setup 脚本, 版本号通过 `/DMyAppVersion=` 传入
- `scripts/build-app.bat` — 本地构建
- `scripts/pack-python-env.bat` — Python 环境打包
- `BUILD.md` — 完整构建文档
