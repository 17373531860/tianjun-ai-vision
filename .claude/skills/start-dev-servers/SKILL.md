---
name: start-dev-servers
description: "本地启动前后端开发服务器的标准流程。覆盖：(1) 启动前必扫端口占用 (2) 副本/main/se9 多 worktree 并行时的端口约定 (3) `.env.development.local`（⚠️ 不是 .env.local）把前端 axios baseURL 钉到本副本后端避免污染主 DB (4) **后端必须用 tianjun conda env 启动**（直接 `python` 走 base 缺 onnx/tensorrt） (5) 后端 8002 + 前端 6002 标准对位 (6) 进程清理 / setsid 后台 fork 模板 (7) /docs + /api/v1/projects 健康检查 + 浏览器 console 看 [API] Final baseURL 埋点 + readlink /proc/PID/exe 看 python 解释器。当用户说『启动前端』『起一下后端』『跑一下开发环境』『先启服务测试』『端口被占了』『前端连不上后端』『副本环境怎么搭』『baseURL 错了』『8001 被占了用什么端口』『前端调到 main 的后端去了』『DB 污染』『多个 worktree 怎么同时跑』『进程没退干净』『加载项目列表失败』『Network Error』『.env.local 没生效』『ONNX/TensorRT 未安装』『推理格式选不到』『main 能用副本不能用』时触发。**任何启动前后端的任务，必须先走第二节『启动前必做清单』再发命令。** 违反任一条会撞端口冲突、DB 写错库、前端 axios 调到另一个 worktree 的后端、后端跑错 conda env 缺依赖。"
allowed-tools: "Read, Grep, Glob, Shell, Write, StrReplace"
---

# start-dev-servers: 本地启动前后端的标准流程

> 用户提到「启动 / 起 / 跑开发环境」类任务时，**先读第二节『启动前必做清单』里 5 条强制检查**，确认每一条都过了再发启动命令。

---

## 一、本仓库的端口/Worktree 约定（必看，不要乱选端口）

本仓库长期同时存在 3 个 worktree：

| Worktree 物理路径 | 默认前端端口 (vite) | 默认后端端口 (uvicorn) | 用途 |
|---|---|---|---|
| `/桌面/word/tianjun-main` | **5173** | **8001** | 主线，稳定版 |
| `/桌面/word/tianjun副本` | **6001 / 6002** | **8002** | 副本，开发/实验 |
| `/桌面/word/tianjun-se9` | 6003（按需） | 8003（按需） | SE9 ARM64 平台线 |

**铁律**：

1. **不要让副本去占 8001 / 5173** — 那是 main 的家
2. **同一 worktree 内端口要成对**：副本前端 6002 ↔ 副本后端 8002
3. **vite 不会强制端口**：`vite.config.js` 配的是默认 6001，被占自动跳 6002 / 6003 / ... — 启动后看 vite log 输出的 URL 才是真实端口
4. **uvicorn 端口写死在启动命令里**：不会跳，被占就直接 `Address already in use` 退出

---

## 二、启动前必做清单（5 条强制检查）

按顺序逐项检查，**任何一条没过都不要发启动命令**。

### 检查 1：所在 worktree 与目标端口对得上

```bash
pwd
# 副本 → 应使用 6002 / 8002
# main → 应使用 5173 / 8001
```

如果 cwd 在副本但要启 5173 / 8001 → **错**，会撞 main。

### 检查 2：扫描目标端口占用

```bash
# 一行查多端口（curl 探测 + 进程列举）
echo '=== 端口监听 ==='
ss -tln 2>/dev/null | awk 'NR==1 || /:(5173|6001|6002|6003|8001|8002|8003)\b/'

echo '=== 相关进程 ==='
pgrep -af 'vite|npm run dev|uvicorn' 2>/dev/null

echo '=== HTTP 响应探测（被占的端口会返回非 000）==='
for port in 6002 8002; do
  curl -s -o /dev/null -w "$port: HTTP %{http_code}\n" --max-time 2 "http://localhost:$port/" || true
done
```

判断规则：

- **HTTP 000** → curl exit 7 = 连接被拒 = **端口空闲，可启**
- **HTTP 200/3xx/4xx** → 端口已被某进程占用 — **必须先识别是谁**：
  - 如果是「同 worktree 的同名服务还活着」→ 复用，不重启
  - 如果是「另一个 worktree」→ 切到本 worktree 的目标端口（6002 → 6003）
  - 如果是「无关进程」→ 停下来问用户怎么处置，**不要擅自 kill**

### 检查 3：识别占用进程的所属 worktree（防止误杀 main）

```bash
# 拿到进程后查 cwd
ps -p <PID> -o pid,ppid,cwd,cmd
# 或
readlink /proc/<PID>/cwd
```

如果占 8001 的 uvicorn cwd 在 `tianjun-main` → 是 main 的后端，不要碰。

### 检查 4：副本/se9 worktree 必须有 `.env.development.local` 把 axios baseURL 钉对

**这是最容易踩的坑** —— 前端 `frontend/.env.development` 写死 `VITE_API_BASE_URL=http://localhost:8001/api/v1`（指向 main 后端）。副本启动若不覆盖，会导致：

```
副本前端 6002 → axios 仍打 8001 → 写到 main 的 sql_app.db → 污染主 DB
```

#### ⚠️ vite env 加载优先级陷阱（v3.10.x 实测踩过）

vite 加载多个 `.env*` 文件时，**后加载的覆盖前面的**：

```
.env                  ← 最先加载
.env.local
.env.[mode]           ← 比 .env.local 优先级高 ⚠️
.env.[mode].local     ← 最高 ✓
```

**错的做法**：创建 `.env.local` 写 `VITE_API_BASE_URL=http://localhost:8002/api/v1`
→ 仍被后加载的 `.env.development` (mode-specific) 覆盖回 `http://localhost:8001/api/v1`
→ baseURL 不生效，浏览器仍调 8001

**对的做法**：创建 `.env.development.local`（mode + .local 双特性）才能覆盖 `.env.development`。

#### 操作步骤

确认或创建 `.env.development.local`（被 `.gitignore` 默认覆盖，不入版本控制）：

```bash
ls frontend/.env.development.local 2>/dev/null && cat frontend/.env.development.local \
  || echo "缺 .env.development.local, 需要创建"
```

若缺，创建模板：

```bash
cat > frontend/.env.development.local <<'EOF'
# 本地开发覆盖 - <worktree-name> 专用 (gitignored)
# 把 axios baseURL 钉到本副本后端，隔离 main 的 DB
# 注意：必须是 .env.development.local 不是 .env.local，
# 因为 .env.development 优先级高于 .env.local
VITE_API_BASE_URL=http://localhost:8002/api/v1
EOF
```

#### 验证 env 是否生效（必做！）

vite 读 env 只在进程启动时读一次，改完 env 文件必须**重启 vite**才生效（HMR 不会自动 reload env）。重启后在浏览器 console 看埋点输出：

```
[API] Final baseURL: http://localhost:8002/api/v1   ← 应是 8002, 不是 8001
```

埋点位置：`frontend/src/api/index.js:54`，代码：
```js
const baseURL = getBaseURL();
console.log('[API] Final baseURL:', baseURL);
```

如果显示 8001 → env 没生效，回头检查文件名是否真的是 `.env.development.local`（不是 `.env.local`），以及是否重启了 vite。

### 检查 5：后端**必须**用 `tianjun` conda env 启动（不是 base！）

这是 **v3.10.x 实测踩过的最大坑** —— 直接用 `python` 命令默认走 anaconda **base** env 的 Python 3.13，**缺关键依赖** (`onnx`, `tensorrt`)，导致：

- 推理格式选择对话框里 ONNX Runtime / TensorRT 都标"未安装"
- 不能转换 `.onnx` 模型 / 不能加载 TensorRT engine
- 现象隐蔽 — 后端能正常启动、UI 能用，只是部分功能"消失"了

正确做法：**用 `tianjun` env 的绝对路径 Python 启动**：

```bash
/home/qianqian/anaconda3/envs/tianjun/bin/python -u -m uvicorn backend.main:app \
  --host 0.0.0.0 --port <port> ...
```

启动前验证 env 正确：

```bash
# 1. 确认 tianjun env 存在
conda env list | grep tianjun

# 2. 确认 tianjun env 有完整依赖（应全部 ✓）
/home/qianqian/anaconda3/envs/tianjun/bin/python -c "
for lib in ['onnx','onnxruntime','tensorrt','torch','fastapi','uvicorn']:
    try: __import__(lib); print(f'  ✓ {lib}')
    except ImportError: print(f'  ✗ {lib} 缺')
"

# 3. 确认 Python 是 3.10（与 AGENTS.md §二一致）
/home/qianqian/anaconda3/envs/tianjun/bin/python --version
```

启动后**立刻验证**实际跑的解释器是 tianjun（不是 base）：

```bash
new_pid=$(pgrep -f 'uvicorn.*<port>' | head -1)
readlink /proc/$new_pid/exe
# 期望: /home/qianqian/anaconda3/envs/tianjun/bin/python3.10
# 错误: /home/qianqian/anaconda3/bin/python3.13 (这是 base 不是 tianjun)
```

### 检查 6：副本/se9 worktree 用独立 `sql_app.db`（避免 SQLite WAL 锁冲突）

```bash
# 副本后端实际用 backend/sql_app.db (不是根目录的 sql_app.db 占位文件)
# 启动 log 会有 [DIAG] DB URI = sqlite:///.../backend/sql_app.db
stat -c '%i %n' \
  /home/qianqian/桌面/word/tianjun副本/backend/sql_app.db \
  /home/qianqian/桌面/word/tianjun-main/backend/sql_app.db
# 两个 inode 应不同 → 独立文件 → 安全
```

如果某个 worktree 缺 `backend/sql_app.db`，第一次启动后端会自动创建 + 跑 `migrate_database()` 建 35 张表，约 5-10 秒。

---

## 三、标准启动命令模板

### 后端 (uvicorn) — ⚠️ 必须用 tianjun env 的绝对路径 Python

```bash
cd /home/qianqian/桌面/word/tianjun<副本|main|-se9>
setsid -f /home/qianqian/anaconda3/envs/tianjun/bin/python -u -m uvicorn backend.main:app \
  --host 0.0.0.0 --port <8001|8002|8003> \
  > /tmp/tianjun_<worktree>_backend.log 2>&1 < /dev/null
```

- `/home/qianqian/anaconda3/envs/tianjun/bin/python` → **绝对路径**，钉死 tianjun env（不是 base）。**绝对不要直接写 `python`**，那会走 base env 缺 onnx/tensorrt
- `setsid -f` → 完全脱离父 shell，agent shell 退出不会带走后端
- `python -u` → unbuffered，日志即时刷盘
- 重定向 stdin → `< /dev/null` 避免后端被 SIGHUP

### 前端 (vite)

```bash
cd /home/qianqian/桌面/word/tianjun<副本|main|-se9>/frontend
setsid -f npm run dev \
  > /tmp/tianjun_<worktree>_frontend.log 2>&1 < /dev/null
```

vite 自动选端口，启动后看 log 拿到真实 URL：

```bash
sleep 8 && tail -10 /tmp/tianjun_<worktree>_frontend.log
# 输出会有: ➜ Local: http://localhost:<port>/
```

---

## 四、启动后健康检查（4 条）

```bash
# 1. 后端进程在
pgrep -af 'uvicorn.*<port>' || echo "后端没起来！"

# 2. 后端 /docs 200（Swagger 自带，永不需要鉴权）
curl -s -o /dev/null -w 'docs: HTTP %{http_code}\n' --max-time 3 http://localhost:<be_port>/docs
# 期望 HTTP 200

# 3. 后端 /api/v1/projects/ 200 或 307（v3.10 起鉴权未开默认 200）
curl -s -o /dev/null -w 'projects: HTTP %{http_code}\n' --max-time 3 http://localhost:<be_port>/api/v1/projects/
# 期望 HTTP 200 或 HTTP 307 (trailing slash 重定向)

# 4. 前端首页 200
curl -s -o /dev/null -w 'frontend: HTTP %{http_code}\n' --max-time 3 http://localhost:<fe_port>/
# 期望 HTTP 200, 返回 ~400 字节 HTML
```

任何一条不过 → 看 `/tmp/tianjun_<worktree>_<service>.log` tail 找根因，不要直接重启。

---

## 五、常见故障 / 历史踩坑

### 故障 A：vite 启在了非预期端口

**现象**：log 写 `Local: http://localhost:6003/` 但你要 6002。

**根因**：6002 已被某进程占。看 vite log 上方的 `Port 6002 is in use, trying another one...`。

**修复**：
1. `pgrep -af vite` 找到占 6002 的进程，识别 worktree
2. 如果是同 worktree 旧实例 → `kill <pid>` 后重启
3. 如果是别 worktree → 接受 vite 跳的新端口（前端端口变化**不**影响 baseURL，因为 baseURL 指向**后端**端口；只在后端端口变化时才要改 `.env.development.local`）

### 故障 B：前端能跑但调 API 都失败 / 调到了别的 worktree 的后端

**现象**：前端 6002 跑得好，但项目列表/检测结果都空（"加载项目列表失败"红条），或者数据莫名出现在 main 那边。浏览器 console 看到 `[API] Final baseURL: http://localhost:8001/api/v1`。

**根因（按出现概率排序）**：

1. **❌ 用了 `.env.local` 而不是 `.env.development.local`** — vite 加载优先级里 `.env.development` (mode-specific) **会覆盖** `.env.local`，结果 baseURL 还是 main 的 8001。**这是 v3.10.x 期间踩过的真实坑，每个新进副本/se9 worktree 的开发都会踩**。修复见检查 4。
2. **改完 env 没重启 vite** — env 在 vite 启动时一次性读入并编译进 JS bundle，HMR 不重新加载 env。改完 env 必须 `kill <vite-pid> && npm run dev`。
3. **浏览器缓存了旧 JS bundle** — 浏览器可能用之前 vite 编译的 bundle。修复：浏览器硬刷新（Ctrl+Shift+R）或新开匿名窗口。

**诊断步骤**：

```bash
# 1. 确认正确的覆盖文件存在
ls frontend/.env.development.local && cat frontend/.env.development.local

# 2. 浏览器 console 看埋点（应显示 8002 不是 8001）
# [API] Final baseURL: http://localhost:8002/api/v1

# 3. 浏览器 Network tab 看实际请求 URL（确认打到 8002）
# GET http://localhost:8002/api/v1/projects
```

**修复**：见第二节检查 4。修完文件名后**必须重启 vite**，然后浏览器**硬刷新**。

### 故障 C：后端启动卡住 / `/dev/ttyUSB0` 错误刷屏

**现象**：后端日志大量 `[ExtDev] /dev/ttyUSB0 打开失败`。

**根因**：开发机没接称重器/串口外设，但 DB 里有 `external_devices` 记录配了 USB 串口。

**判断是否阻塞**：看是否有 `Uvicorn running on http://0.0.0.0:<port>` 行 — **有就说明启动成功**，串口错误只是后台重试线程的噪音，可以无视。

**根治**（可选）：进 Settings → 外部设备 把无硬件的设备记录删掉，或直接 `UPDATE external_devices SET enabled=0`。

### 故障 D：插件目录不存在错误

**现象**：`[Plugin][internal-demo] startup_load 失败: 插件目录不存在: backend/plugins/internal-demo`。

**根因**：v3.10+ 插件系统在 DB 里登记了占位插件，但 worktree 没建对应物理目录。

**判断**：和故障 C 一样 — 看是否有 `Uvicorn running on ...` 行 → 有就成功，这个 ERROR 不阻塞。

**根治**：`UPDATE plugin_registrations SET active=0 WHERE name='internal-demo'` 或建空目录占位。

### 故障 E：APScheduler 未安装 → 定时导出关闭

**现象**：`[Scheduled] APScheduler 未安装, 定时导出功能关闭`。

**根因**：开发机的 Python 环境没装这个可选依赖。

**判断**：不阻塞主功能。本机要测定时导出才需要 `pip install apscheduler`。

### 故障 F：副本前后端起了但前端报 CORS

**现象**：浏览器 console 报 `Access-Control-Allow-Origin`。

**根因**：后端 `CORS_ALLOW_ORIGINS` 环境变量没设，默认只放行 `localhost:5173`，不放 6002。

**修复**：启后端时加：

```bash
CORS_ALLOW_ORIGINS='http://localhost:6002,http://localhost:5173' \
  setsid -f python -u -m uvicorn backend.main:app --host 0.0.0.0 --port 8002 ...
```

或代码里检查 `backend/main.py` 的 CORS middleware 是否已经 `allow_origins=["*"]`。

### 故障 H：推理格式选择对话框里 ONNX / TensorRT 标"未安装"，但你记得装过

**现象**：模型管理 → 转换格式 → 弹窗里 `ONNX Runtime + CUDA` 和 `TensorRT FP32` 都标"模块未安装"，但在 main 那边用同样的 worktree 能用。

**根因**：**后端跑在 anaconda base env 的 Python 3.13**（缺 onnx/tensorrt），不是 `tianjun` env 的 3.10（依赖齐全）。直接用 `python` 命令启动会走 base，必须用 tianjun env 的绝对路径。

**诊断**：

```bash
new_pid=$(pgrep -f 'uvicorn.*<port>' | head -1)
readlink /proc/$new_pid/exe
# 错的: /home/qianqian/anaconda3/bin/python3.13 (base)
# 对的: /home/qianqian/anaconda3/envs/tianjun/bin/python3.10 (tianjun)
```

**修复**：

```bash
# 1. 杀掉错 env 的后端
kill <pid>
sleep 2

# 2. 用 tianjun env 重启（见第三节标准模板）
setsid -f /home/qianqian/anaconda3/envs/tianjun/bin/python -u \
  -m uvicorn backend.main:app --host 0.0.0.0 --port <port> ...

# 3. 浏览器硬刷新（旧的"未安装"标记会消失）
```

**为什么会用错 env**：项目 AGENTS.md §二明确写了 conda env 是 `tianjun`，但开发者习惯性敲 `python` 命令 → shell PATH 优先级匹配 anaconda base → 走错 env。要么把 tianjun env 加进 shell rc 自动激活，要么记住用绝对路径。

### 故障 G：副本 worktree 启动后端污染 main 的 DB

**现象**：在副本里测了一通项目改动，结果 main 那边项目列表里也出现了。

**根因**：**双重失误** —— 一是没设 `TIANJUN_DATA_DIR`（默认 `BASE_DIR`，安全），二是手动 `cp` 了 main 的 db 文件过来或者通过软链共享。

**修复**：
1. 删除副本目录的 `sql_app.db` + `sql_app.db-wal` + `sql_app.db-shm`
2. 重启副本后端，会自动初始化新的空库
3. 用 `stat -c '%i %n'` 确认 inode 与 main 的 db 文件不同

---

## 六、退出/清理（保持环境整洁）

```bash
# 优雅停 + 端口验证
pkill -f 'uvicorn.*<port>'          # 杀本 worktree 后端
pkill -f 'vite.*<worktree-path>'    # 杀本 worktree 前端
sleep 2
# 验证端口已释放
curl -s -o /dev/null -w '%{http_code}\n' --max-time 1 http://localhost:<port>/  # 应返回 000
```

**禁止**：

- `pkill -9 -f vite` 不带路径限定 → 会一并打死 main 那边的 vite，影响其他 agent
- `lsof -ti:8002 | xargs kill` 在多 worktree 场景下不可靠（端口和 worktree 不是 1:1 绑死）

---

## 七、给 agent 的快速决策树

```
用户说『启动前后端』『起一下服务』『跑一下测试环境』
  │
  ▼
1. 看 cwd 在哪个 worktree (pwd)
2. 查对应的目标端口（第一节表）
3. 扫端口（第二节检查 2）
   │
   ├─ 端口全空 → 直接走标准启动模板（第三节）
   │              + 启动后健康检查（第四节）
   │
   ├─ 端口部分被占（同 worktree）→ 直接复用，不重启
   │
   ├─ 端口被别 worktree 占 → 用 vite 自动跳的新端口
   │                          → 前端端口变了不影响 baseURL（baseURL 钉的是后端端口）
   │                          → 只有**后端**端口变了才需要改 `.env.development.local`
   │
   └─ 端口被无关进程占 → 停下问用户，不擅自 kill
```

---

## 八、关键文件速查

| 文件 | 作用 |
|---|---|
| `frontend/.env.development` | git tracked，写死 `VITE_API_BASE_URL=http://localhost:8001/api/v1`（默认值，给 main worktree 用）|
| `frontend/.env.development.local` | **每个非 main worktree 必备**，gitignored，覆盖 baseURL 指向本 worktree 的后端。⚠️ **不能用 `.env.local`** — 那个优先级低于 `.env.development`，写了等于没写 |
| `frontend/.gitignore` | 已默认 `*.local` 忽略所有 `.local` 后缀文件，不会误入 commit |
| `frontend/vite.config.js` | vite 默认端口配在这（一般 6001）|
| `backend/core/config.py` | `DATA_DIR = TIANJUN_DATA_DIR or BASE_DIR` — 默认每个 worktree 用自己 cwd 的 `backend/sql_app.db` |
| `backend/main.py: migrate_database()` | 空库启动时自动建 35 张表 + 60+ ALTER |
| `/home/qianqian/anaconda3/envs/tianjun/bin/python` | **唯一正确的后端 Python 解释器** (3.10 + onnx + tensorrt + torch+cu126 等齐全)。绝对不要用裸 `python` 命令 |
| `/tmp/tianjun_<worktree>_<service>.log` | 推荐的后台进程 log 路径约定 |

---

## 九、最后的提醒

- 启动是发命令的几秒钟，**风险是几小时的 DB 污染 / 端口冲突 / 杀错 main 的进程**
- 第二节 5 条检查每一条都是用真实事故沉淀出来的，不要为了快跳过
- 启动成功不是终点 — **第四节 4 条健康检查通过才算交付**
