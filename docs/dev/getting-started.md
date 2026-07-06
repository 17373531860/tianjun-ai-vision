# Getting Started：从零跑通一次检测

> **类型**：tutorial（本目录唯一一篇教程）
> **本文不讲**：端口冲突排障、多 worktree 隔离（去看 `start-dev-servers` skill）；测试体系（去看 [quality-map.md](quality-map.md)）。
> **与代码冲突时**：以代码为准，发现请顺手修本文。

目标：30 分钟内在开发机上启动前后端，用**虚拟剧本源**（无摄像头、无模型）跑通一次真实检测周期，并在数据页看到这条记录。做完你就摸过了整条主链路。

## 第 0 步：确认环境

本项目后端**必须**跑在 conda 环境 `tianjun`（Python 3.10，torch/onnx/tensorrt 齐全）。裸 `python` 会走 base 环境，功能会静默缺失。

```bash
conda env list | grep tianjun
/home/qianqian/anaconda3/envs/tianjun/bin/python --version   # 应为 3.10.x
cd frontend && npm install && cd ..                          # 首次拉库后装前端依赖
```

## 第 1 步：启动后端（端口 8001）

```bash
cd /home/qianqian/桌面/word/tianjun-main
RUNTIME_MODE=test setsid -f /home/qianqian/anaconda3/envs/tianjun/bin/python -u \
  -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 \
  > /tmp/tianjun_main_backend.log 2>&1 < /dev/null
```

`RUNTIME_MODE=test` 会额外挂上虚拟检测剧本端点（生产环境不挂）。等日志出现 `Uvicorn running on http://0.0.0.0:8001` 即成功——日志里的串口/插件报错是无硬件噪音，不阻塞（判断方法见 `start-dev-servers` skill 第五节）。

验证：

```bash
curl -s -o /dev/null -w 'docs: %{http_code}\n' http://localhost:8001/docs          # 200
curl -s -o /dev/null -w 'projects: %{http_code}\n' http://localhost:8001/api/v1/projects/  # 200
```

## 第 2 步：启动前端（端口 5173）

```bash
cd frontend && setsid -f npm run dev > /tmp/tianjun_main_frontend.log 2>&1 < /dev/null
sleep 8 && grep "Local:" /tmp/tianjun_main_frontend.log   # 拿真实 URL
```

浏览器打开该 URL。按 F12 在 console 里确认一行 `[API] Final baseURL: http://localhost:8001/api/v1`——这行不对，后面全白做。

## 第 3 步：用虚拟剧本跑一次检测

不接摄像头、不加载模型、不用手建项目——让后端吃一段预录的"检测结果流"，并让它自动套一个最小顺序模式项目配置（`with_project: true`）：

```bash
curl -s -X POST http://localhost:8001/api/v1/test/synthetic/start \
  -H 'Content-Type: application/json' \
  -d '{"scenario": "ok_sequential_cycle.json", "with_project": true}'
```

剧本文件在 `tests/scenarios/ok_sequential_cycle.json`：`step_a → step_b → step_c` 三个标签各连续出现 30 帧后消失，预期产生一个完整 OK 周期。此时切到前端「监控」页能看到步骤卡片依次点亮、周期结束打出 OK 事件。观察剧本运行状态与跑完停掉：

```bash
curl -s "http://localhost:8001/api/v1/test/synthetic/state"     # 看时间线进度
curl -s -X POST "http://localhost:8001/api/v1/test/synthetic/stop"
```

## 第 4 步：验证数据落库（双向验证的习惯从第一天养成）

前端「数据」页应出现刚才那条周期记录。再从 API 侧确认一次：

```bash
curl -s "http://localhost:8001/api/v1/data/sessions?limit=5" | head -c 600
```

看到最新 session（可再用 `/api/v1/data/sessions/{id}/cycles` 取它挂的周期），说明"采集→状态机→结算→落库→前端展示"整条链路你已经全程跑通。

## 你刚才经过了哪些代码

| 环节 | 代码位置 |
|---|---|
| 路由挂载 | `backend/api/router_manifest.py` |
| 虚拟剧本源 | `backend/api/test_runtime_routes.py` + `backend/api/source_synthetic_mixin.py` + `tests/scenarios/` |
| 检测状态机 | `backend/api/source.py` + `source_*_mixin.py`（深潜见 [internals/source-state-machine.md](internals/source-state-machine.md)） |
| 周期/步骤落库 | `backend/api/sessions.py`、表结构见 [reference/db-schema.md](reference/db-schema.md) |
| 监控页 | `frontend/src/views/Monitor/index.vue` |

## 下一步

- 建立全局心智模型 → [architecture/](architecture/)
- 要动手改代码 → 回 `AGENTS.md` 第四节按场景进对应 skill，动手前读第三节工作守则（T0-T8 完成定义）
- 收尾清理：`pkill -f 'uvicorn.*8001'; pkill -f 'vite.*tianjun-main'`
