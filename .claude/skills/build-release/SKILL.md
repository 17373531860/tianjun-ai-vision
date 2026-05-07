---
name: build-release
description: "打包发版全流程指南：版本号更新、CI流程、Nuitka编译、conda-pack、Inno Setup安装包、GitHub/Gitee发布。当需要发布新版本或排查构建问题时使用。"
argument-hint: "[版本号或构建问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# build-release: 打包发版流程

天军 AI 视觉检测系统 v3.5.x 主线 CI/CD 全景说明。
当前线上版本：**v3.5.1**，权威源 `electron/package.json`。

需求: $ARGUMENTS

---

## 一、版本号单一权威源

`electron/package.json` 的 `.version` 字段是**唯一**版本来源。CI 直接 `Get-Content -Raw | ConvertFrom-Json` 取值，再 `/DMyAppVersion=` 传给 Inno Setup。

需要同步的位置：

| 位置 | 用途 | 同步方式 |
|---|---|---|
| `electron/package.json` `.version` | 权威源，CI 读这里 | 手改 |
| `electron/splash.html` 的 `.version` div（line 112） | 启动画面显示 `v3.5.1` | 手改，必须和 package.json 一致 |
| Git tag `vX.Y.Z` | 触发 Release 通道 + 一致性校验 | `git tag -a vX.Y.Z` |
| `docs/CHANGELOG.md` 顶部 | 总览 | 手改 |
| `docs/changelog/vX.Y.Z_*.md` + `.json` | 单版本详细 changelog | `update-release` skill 生成 |

**CI 强制 tag ↔ package.json 一致性校验**（`build.yml` ~270 行）：
```powershell
$pkg = Get-Content "electron/package.json" -Raw | ConvertFrom-Json
$version = $pkg.version
if ($refName -like "v*" -and $refName.TrimStart("v") -ne $version) {
  echo "ERROR: tag 与 package.json 不一致"; exit 1
}
```
不一致直接中止构建，杜绝"tag=v3.5.1 但打出来的包是 v3.5.0"这种事故（v2.0.8 → v2.7.11 半年间发生过一次，详见下文"历史踩坑"）。

---

## 二、CI 构建流程概览（`.github/workflows/build.yml`）

### 触发条件

| 事件 | Action Artifact（完整 exe） | GitHub Release（分卷） |
|---|---|---|
| `push` 到 `main` / `master` | 是 | 否 |
| `push` tag `v*` | 是 | **是** |
| `workflow_dispatch` 手动 | 是 | 否 |

> 注意：PR 不触发 CI（`on:` 只声明了 `push.tags` / `push.branches` / `workflow_dispatch`）。

### 主流程（windows-latest，`timeout-minutes: 300`）

```
1. 释放磁盘（删 dotnet/VS 2019/CodeQL/Java tool cache）
2. checkout + setup Node 20 + setup Miniconda（env 名 tianjun, python 3.10）
3. Python 依赖（PyTorch cu128 + ONNX-GPU + TensorRT + scipy + mediapipe --no-deps + numpy<2 + opencv-contrib-python<4.11 + conda-pack）
4. Nuitka 编译核心 .py → .pyd（重要细节见第三节）
5. conda-pack tianjun → python-env/python/（解压后约 5-6 GB）
6. 下载 FFmpeg win64 GPL（github.com/BtbN/FFmpeg-Builds）
7. Vite 前端构建（cd frontend && npm ci && npm run build）
8. electron-builder --dir --x64（生成 win-unpacked/，不出 Setup）
9. 装 Inno Setup 6（choco install innosetup -y）
10. ISCC 打包 → TianJun-AI-Vision-{package.json.version}-Setup.exe（约 3.75 GB）
11. [通道 A] 上传完整 exe 为 Action Artifact（30 天保留）
12. [通道 B，仅 tag] 切 1.9 GB 分卷 + checksums.txt + merge_installer.bat
13. [通道 B，仅 tag] gh release create 上传到 17373531860/tianjun-releases
```

### 关键时长参考
- 完整一轮约 45-60 min。Python 依赖装 10-15 min 是大头，Nuitka 编译每文件几十秒。
- 磁盘清理是必要步骤，windows-latest 默认空间不够装 PyTorch+TensorRT+conda-pack。

---

## 三、Nuitka 编译范围与已知 IP 泄露 BUG

CI 用 `nuitka --module` 把核心 `.py` 编成 `.pyd`，编译成功后**删除源 .py**（`build.yml` 158-163 行）。Runtime 仍 spawn `python -m uvicorn backend.main:app`，加载 `.pyd` + `.py` 混合包。

### CORE_FILES 列表（`build.yml` 136-148 行）

```bash
CORE_FILES=(
  "backend/api/source.py"        # 存在
  "backend/api/detection.py"     # 不存在
  "backend/api/sessions.py"      # 存在
  "backend/api/projects.py"      # 存在
  "backend/api/cameras.py"       # 存在
  "backend/api/alarm.py"         # 存在
  "backend/api/reports.py"       # 存在
  "backend/api/tasks.py"         # 存在
  "backend/api/websocket.py"     # 不存在
  "backend/api/models.py"        # 存在
  "backend/services/detector.py" # 不存在
)
```

### 已知 BUG / IP 泄露风险（**与 AGENTS.md 第九节登记一致**）

1. **11 个文件中 3 个根本不存在**：`api/detection.py` / `api/websocket.py` / `services/detector.py` 早被重构掉但 CI 列表没更新。循环里 `if [ -f "$pyfile" ]` 直接跳过，**实际只编译 8 个**，CI 打 `WARNING: Failed to compile ...` 但 build 不 fail。

2. **`backend/api/source_*.py` 共 35 个 mixin/组件/工具文件全部没编译**。`source.py` 主类编译了，但 15 个继承式 mixin（`source_*_mixin.py`）和 6 个 has-a 组件（`source_drawer.py` / `source_recorder.py` / `source_geometry.py` / `source_inference_executor.py` 等等）都是源码部署到客户工控机。**业务核心逻辑约 80% 是源码可读状态**，是项目最大的 IP 泄露点。

3. **`backend/services/` 目录除 `detector.py`（不存在）外完全没编译**。`services/scanner.py`（1964 行）、`services/mes_hooks.py`（1505 行）、`services/cluster_collector.py`（1122 行）、`services/export_*.py`（8 个）等核心 service 全是源码。

> **修这个 BUG 的代价**：CORE_FILES 加几十个文件 → CI 时长可能 30 min → 60 min+；Nuitka 对继承式 mixin 的 `super().__init__` 链有时编译失败需要降级保留 .py。本 skill 只记录现象，**不要主动改 build.yml**，等用户决策。

### 编译产物验证（编译完打印的清单）
```
echo "Remaining .py files in backend/api/:"   ← 应只有 source_*_mixin.py + 工具
echo "Generated .pyd files in backend/api/:"  ← 应 8 个 .pyd
ls -la backend/api/*.py / *.pyd
ls -la backend/services/*.py / *.pyd
```

---

## 四、打包结构（客户机 `%ProgramFiles%\tianjun-ai-vision\` 解出后）

| 模块 | 来源 | 编译方式 | 体积 |
|---|---|---|---|
| Vue 前端 | `frontend/dist/` | Vite build（JS 压缩混淆） | ~30 MB |
| Python 后端 | `backend/` 全部 | 8 个 .pyd + 大量 .py 源码 | ~50 MB |
| Python 运行时 | conda env `tianjun` | conda-pack tar.gz 解出 | ~5-6 GB（PyTorch+CUDA 占大头） |
| Electron 主进程 | `electron/main.js` 等 | **不**经 Nuitka，源码部署 | ~150 MB（含 chrome 内核） |
| FFmpeg | BtbN 预编译 win64 GPL | 二进制 | ~150 MB |
| 海康/CH340 驱动 | `electron/drivers/CH341SER` | 二进制 | ~5 MB |

`electron/package.json: build.extraResources` 决定哪些文件被打进 `resources/`：
```json
{"from": "../frontend/dist", "to": "app/dist"}
{"from": "../backend",       "to": "backend"}     // 含 .py / .pyd / .dll / .cti / .ax / .ini / .manifest，排除 __pycache__/uploads/recordings/*.db
{"from": "../python-env/python", "to": "python"}
{"from": "../ffmpeg",        "to": "ffmpeg"}      // 只 ffmpeg.exe + ffprobe.exe
{"from": "drivers/CH341SER", "to": "drivers/CH341SER"}
```

`backend-manager.js` 启动后端的唯一方式（line 372-378）：
```js
spawn(pythonPath, ['-m', 'uvicorn', 'backend.main:app',
                   '--host', '0.0.0.0', '--port', this.options.port,
                   '--no-access-log']);
```

### Inno Setup（`electron/build/installer.iss`）
- AppId: `com.tianjun.ai-vision`
- 默认目录：`%ProgramFiles%\tianjun-ai-vision`
- 用户数据：`%APPDATA%\tianjun-ai-vision`（DB / uploads / recordings 都在这）
- 输出：`TianJun-AI-Vision-{#MyAppVersion}-Setup.exe`，64-bit + LZMA2 压缩，约 3.75 GB
- 装机前自动备份 `license.lic`，装机后恢复（防升级丢激活）
- 可选安装 CH340 驱动

---

## 五、发布渠道

### 通道 A — GitHub Action Artifact（内部/团队）
- **不分卷**，完整 `TianJun-AI-Vision-X.Y.Z-Setup.exe` 直传
- 所有 push 都跑（main / tag / workflow_dispatch）
- 30 天自动清理；`continue-on-error: true` → 配额满不阻塞 Release 上传
- 适用：开发机、内部测试、临时分发

### 通道 B — GitHub Release（外部/客户副机，只在 tag 时跑）
- 主仓库 `17373531860/tianjun-ai-vision` 是 **PRIVATE**
- Release 发布到独立 public repo `17373531860/tianjun-releases`
- 走 `secrets.RELEASE_TOKEN`（Fine-grained PAT，90 天到期）
- **必须分卷**：GitHub Release 单文件硬限 2 GB，3.75 GB 安装包切 1.9 GB×N → `*.part00.part / part01.part / ...` + `checksums.txt` + `merge_installer.bat`
- 客户下全部分卷到同一目录，双击 `merge_installer.bat` 合并 + SHA256 校验 + 输出完整 exe
- Release 创建用 `gh release create --cleanup-tag`（删旧 release 一并清理 tag），上传后 5 次重试核对 asset 数

### 通道 C — Gitee（公开下载，国内速度，独立 workflow）
- 仓库 `xu-yanzhi32/tianjun-releases` + `tianjun-releases-2`（双仓负载）
- 由独立 workflow `.github/workflows/gitee-upload.yml` 处理（`workflow_dispatch` 手动触发，输入 version）
- 流程：从 GH Release 下完整 exe → 切更小分块（~100 MB / 块）→ 推 Gitee
- 适用：客户下载（境内不走 GitHub）

> 临时让 Action 公开下载：`gh repo edit --visibility public`；上传完改回 `private`。

---

## 六、关键环境变量（详见 AGENTS.md 第十节）

CI 构建期：
- `PYTHON_VERSION=3.10` / `NODE_VERSION=20`
- `CONDA_SOLVER=classic`（避新解析器卡顿）
- `GH_TOKEN=${{ secrets.RELEASE_TOKEN }}`（仅 Release 步骤注入）

客户机运行期：
- `OPENCV_FFMPEG_CAPTURE_OPTIONS=threads;1`（**`backend/main.py` 第 4 行 setdefault**，必须在 cv2 import 前；v3.1.3 关键保命修复）
- `TIANJUN_DATA_DIR`：用户数据目录，默认 BASE_DIR；客户机 Inno 装机后会指向 `%APPDATA%\tianjun-ai-vision`
- `BACKEND_SKIP_INIT`：跳过初始化（测试/排障）
- `ENABLE_API_DOCS`：默认 `1`，发布版可设 `0` 关 `/docs` `/redoc`
- `CONDA_PREFIX`：开发模式 Python 路径；客户包内不用，由 `backend-manager.js` 直接定位 `resources/python/python.exe`
- `MVCAM_COMMON_RUNENV`：海康工业相机 SDK 路径

---

## 七、依赖锁定版本（CI + 客户端修复脚本均验证通过）

```
torch / torchvision / torchaudio    cu128 (PyTorch 官方 wheels，CUDA 12.8 + Blackwell sm_120)
numpy                               >=1.24.0,<2.0   ← 必须 <2，OpenCV 4.10 ABI
opencv-contrib-python               >=4.8.0,<4.11   ← 必须 <4.11，>=4.11 编时用 numpy 2 ABI
mediapipe                           >=0.10.0,<0.10.21（必须 --no-deps 避免拉 opencv-python）
protobuf                            >=4.25.3,<5     ← mediapipe 限制
onnxruntime-gpu                     最新
tensorrt                            extra-index https://pypi.nvidia.com
scipy                               >=1.10.0
```

**安装顺序硬性要求**（顺序错就 ABI 不兼容）：
```
1. PyTorch cu128
2. backend/requirements.txt
3. onnxruntime-gpu
4. tensorrt
5. scipy
6. mediapipe --no-deps + 手动加 absl-py / attrs / flatbuffers / jax / jaxlib / matplotlib / protobuf<5 / sounddevice / sentencepiece
7. pip uninstall opencv-python opencv-python-headless opencv-contrib-python（避免任何残留）
8. pip install --force-reinstall numpy<2.0（不加 --force-reinstall pip 会跳过同版本）
9. pip install --no-deps opencv-contrib-python<4.11
10. conda-pack
```

CI 编译完会跑 7 项 verify，其中 `cv2.resize` ABI 自检最关键：
```python
img = np.zeros((100,100,3), dtype=np.uint8); cv2.resize(img,(50,50))
```
报 `img is not a numpy array` = numpy/opencv ABI 不兼容，整个构建 fail。

---

## 八、常见构建问题排查

### 问题 1：用户在 GitHub Actions 页看不到 Artifact 下载按钮
- **现象**：跑完 build CI 全绿，但仓库 Actions 页"Artifacts"区域看不到下载按钮
- **根因**：用户**没登录 GitHub** 或没有这个 private repo 的访问权限。Action Artifact 默认只有 repo 协作者可下载
- **解决**：让用户先 `gh auth login`，或临时 `gh repo edit --visibility public`，或走 Release 通道（公开 repo `tianjun-releases`）

### 问题 2：Nuitka 编译失败
- **现象**：CI 日志 `WARNING: Failed to compile backend/api/xxx.py, keeping source`
- **可能原因**：
  - 文件不存在（CORE_FILES 列表过期，参考第三节，已知 3 个）→ build 不 fail，源码部署
  - C 编译器问题：windows-latest 默认带 MSVC Build Tools，理论不缺；本地构建必须装
  - .py 文件 import 链有 Python 版本相关代码（如 walrus、match-case），Nuitka 对应版本支持不全 → 升 Nuitka
  - 模块名冲突（生成 .pyd 时和 site-packages 同名）→ 改源文件名

### 问题 3：conda-pack 体积过大
- **正常**：解压后 5-6 GB（PyTorch cu128 大约 4 GB，CUDA runtime + cuDNN + TensorRT 占大头）
- **超出**：`conda clean -a -y` 已在打包前跑；如果还是大，检查是否有手动 conda install 的多余包（`conda list | wc -l` 应 ~150-200）
- **裁剪策略**：CI 不做（怕漏依赖）；现场可以删 `python/Lib/site-packages/torch/test/`、`torch/include/`，省 ~500 MB

### 问题 4：cv2 ABI 不兼容（客户端报 `img is not a numpy array`）
- **典型**：客户机 `cv2.putText / cv2.resize / cv2.imencode` 报 numpy 类型错
- **根因**：旧版本 conda numpy 是 MKL 编译，pip opencv 是 OpenBLAS；C 层 ABI 不兼容
- **CI 已修**：`pip install --force-reinstall numpy<2.0` + 顺序装 opencv
- **客户端修**：跑 `patch_numpy_opencv_fix.bat`（清华镜像，离线 whl 兜底）。**必须物理删** `site-packages/numpy/` + `numpy.libs/` 再装

### 问题 5：TensorRT 中文路径失败
- **现象**：模型转换报 `[TRT] [E] Input file cannot be found`，但文件实际存在
- **根因**：TensorRT C 库 Windows 上用 ANSI 打开文件，不支持中文
- **解决**：模型文件名/目录全用 ASCII

### 问题 6：tag 与 package.json 不一致
- **现象**：`Build Installer with Inno Setup` 步骤直接 fail：`ERROR: tag vX.Y.Z (=X.Y.Z) 与 electron/package.json (X.Y.W) 不一致`
- **修复**：本地改 `electron/package.json` + `electron/splash.html` → push main → 重打 tag（先 `git tag -d` 删本地旧 tag）→ push 新 tag

### 问题 7：merge_installer.bat 在客户机闪退
- 见 SKILL 历史维护积累的 8 大坑，全部已在 build.yml 367-490 行的脚本生成模板里规避：纯 ASCII + CRLF + 无 BOM、动态探测 part 文件名、`certutil + findstr` 校验、不联用 `setlocal EnableDelayedExpansion + call :Label`
- **现场排障**：让用户右键 bat → 属性 → "解除锁定" 复选框打勾（去除 MoTW），再在 PowerShell 里 `.\merge_installer.bat` 跑能看到错误

### 问题 8：RELEASE_TOKEN 401
- **现象**：tag 触发 CI，最后一步 `gh release create` 报 HTTP 401
- **根因**：Fine-grained PAT 过期（90 天）
- **续期**：登录 GitHub `17373531860` → Settings → Developer settings → Fine-grained tokens → Generate new token
  - Repository access: Only `tianjun-releases`
  - Permissions → Contents: Read and write
  - 拷贝 `github_pat_*` 后 `gh secret set RELEASE_TOKEN --body "<token>" --repo 17373531860/tianjun-ai-vision`

---

## 九、发版检查清单（按顺序打勾）

### 代码阶段
- [ ] 改动已合并到 `main`
- [ ] `electron/package.json` `.version` 改成新版本号（**唯一权威源**）
- [ ] `electron/splash.html` 第 112 行 `v<版本>` 同步
- [ ] 写 `docs/changelog/vX.Y.Z_日期.md` + 同名 `.json`（用 `update-release` skill 生成）
- [ ] `docs/CHANGELOG.md` 顶部追加新版本条目
- [ ] 相关 skill 更新（debug-* / 本 skill）
- [ ] 依赖未变，仍是 numpy<2 / opencv-contrib<4.11 / mediapipe<0.10.21 / protobuf<5

### 认证阶段
- [ ] `gh auth status`，过期则 `gh auth login -h github.com -p https -w -s workflow`
- [ ] `gh auth setup-git`
- [ ] `git remote -v` 确认 URL 干净（无嵌入 token）
- [ ] `RELEASE_TOKEN` Secret 未过期

### 推送阶段
- [ ] 主仓库临时 public（让 Action 跑流量）：`gh repo edit --visibility public --accept-visibility-change-consequences`
- [ ] 选择性 `git add`（**不要**把 .pcapng / test_*.py / .tmp_audit/ 提进去）
- [ ] `git commit -m "release: vX.Y.Z - ..."`
- [ ] `git push origin main`
- [ ] `git tag -a vX.Y.Z -m "vX.Y.Z: ..."`（tag 版本必须等于 package.json）
- [ ] `git push origin vX.Y.Z`

### CI 阶段（约 45-60 min）
- [ ] `gh run list --limit 3` 确认 push main + push tag 各触发一条 run
- [ ] `gh run view <id>` 各步骤全绿，特别是 Inno Setup 步的 tag/version 一致性校验
- [ ] Artifact 下到本地，文件名前缀必须 `TianJun-AI-Vision-X.Y.Z-Setup.exe`，体积 ~3.75 GB
- [ ] Release：`gh release view vX.Y.Z --repo 17373531860/tianjun-releases` 应有 N 个 `.part` + `checksums.txt` + `merge_installer.bat`

### Gitee 同步（手动）
- [ ] `gh workflow run gitee-upload.yml -f version=vX.Y.Z`，等绿
- [ ] 验证 `xu-yanzhi32/tianjun-releases` 上有新版本

### 收尾
- [ ] 主仓库改回 private：`gh repo edit --visibility private --accept-visibility-change-consequences`
- [ ] 内部下 Action Artifact 装机；客户给 Gitee 链接
- [ ] **装后冒烟**：装好启动一次，检查 splash 版本号 + "帮助 → 关于" + 后端 `/api/v1/system/...` 返回正确

---

## 十、历史踩坑（重要事件）

### v2.0.8 → v2.7.11 半年的版本号惨案（2026-04-22 修）
**症状**：tag v2.1 ~ v2.7.11 期间，副机下到 Action Artifact 的安装包文件名永远 `TianJun-AI-Vision-2.0.8-Setup.exe`。
**根因**：旧 `build.yml` 用 `${{ github.ref_name }}.TrimStart("v")` 取版本，push main 时 ref_name=main，TrimStart 不去字符 → fallback 写死 `2.0.8`。
**修复**：版本号改从 `electron/package.json` 读，tag 只做触发 + 一致性校验。
**教训**：Action Artifact 装机后**必须**核对文件名版本号 = tag 版本号。

### merge_installer.bat 的 8 个坑（每条都翻车过）
1. UTF-8 BOM 让 `@echo off` 失效 → 用 `[IO.File]::WriteAllBytes` + ASCII
2. 嵌 `powershell -Command` 单行命令引号互打架 → 纯 cmd 内置命令
3. 硬编码版本号 → 用 `dir /b *.part00.part` 动态探测
4. `setlocal EnableDelayedExpansion` 联用 `call :Label` 参数展开诡异 → 二选一
5. echo 文本含 `( ) | & !` 被 cmd 提前解析 → 全 ASCII 字母 + `- _ : . [ ]`
6. 双击 cwd 不一定是 bat 所在目录 → 第一行 `cd /d "%~dp0"`
7. certutil 中文系统输出多行/分隔符 → `findstr /i /c:"%HASH%"` 不 parse
8. MoTW / Defender 拦 → 让用户右键 → 属性 → "解除锁定"

### Action 也搞分卷的反面教材（v2.7.11 当时）
当时把 Action Artifact 也切成分卷，结果：
- 内部下载多此一举（Artifact 没 2 GB 限制）
- 用户多一步合并 → 多一个出错点
- 分卷文件名暴露上面 v2.0.8 那个 bug

**结论**：Action 永远整包，只有 GH Release 切分卷。

---

## 十一、关键文件

| 文件 | 作用 |
|---|---|
| `.github/workflows/build.yml` | 主 CI（~590 行） |
| `.github/workflows/gitee-upload.yml` | Gitee 同步（手动 dispatch） |
| `electron/package.json` | **版本号唯一权威源** + electron-builder 配置 |
| `electron/splash.html` | 启动画面，版本号显示 |
| `electron/build/installer.iss` | Inno Setup 脚本（~145 行） |
| `electron/backend-manager.js` | spawn `python -m uvicorn backend.main:app` |
| `backend/main.py` | 后端入口 + 60+ ALTER TABLE 迁移 |
| `backend/requirements.txt` | Python 依赖清单 |
| `docs/CHANGELOG.md` | 总览 changelog |
| `docs/changelog/vX.Y.Z_*.md` + `.json` | 单版本 changelog |
| `scripts/build-app.bat` | 本地一键构建（不走 CI） |
| `scripts/pack-python-env.bat` | 本地 conda-pack |
| `BUILD.md` | 完整构建文档 |

---

## 十二、与其它 skill 的边界

- `update-release`：写 changelog、bump 版本号、做 git commit + tag。版本号怎么写、changelog 怎么排，看那边。
- `create-hotfix`：不走完整发版（不 bump version、不重打安装包），给客户下发 `.bat` 补丁脚本 + 替换 .py 文件。CRLF 编码 + Windows 兼容性是那边核心。
- 本 skill 只管"CI 跑了什么 / 为什么慢 / 为什么 fail / 装包结构"。
