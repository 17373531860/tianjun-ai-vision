# 传感器清洁工序插件（sensor-clean）

> Tier 3 全栈插件 · customer_code = `sensor-clean`
>
> 把「传感器清洁」demo 的双视角检测 + 棉签寿命约束搬进天军主程序，**检测与计数复用主程序引擎，插件只做耗材约束与跨视角联动**。

---

## 一、业务背景

来源于 demo（`detect（视角1用）.py`）：

- **视角1**：检测「查看产品有无脏污 / 擦拭产品」两个动作。一个产品被这两个动作覆盖后离开镜头，即计一件。
- **视角2**：检测「更换棉签」动作。
- **耗材约束**：一根棉签擦满 **K** 个产品（demo 默认 11）后必须更换，否则锁定计数；视角2 检出换棉签后自动解锁清零。

---

## 二、架构（A 重构式）

```
主程序（不改源码）                          本插件
┌────────────────────────────┐          ┌──────────────────────────┐
│ 视角1 通道：跟踪模式·事件计数 │  OK 事件  │ event_fire 钩子           │
│   查看脏污 / 擦拭产品         │ ───────► │  → 棉签已用 +1            │
│                            │          │  → 达 K 锁定 + 报警提示    │
│ 视角2 通道：事件计数         │ 换棉签事件 │  → 视角2 换棉签 → 解锁清零 │
└────────────────────────────┘          └──────────────────────────┘
                                          + 双视角监控面板（前端）
                                          + 一键应用对齐 demo 的项目模板
```

- **检测/计数**：靠主程序「跟踪模式 + 事件计数」实现，不写任何 `source_*_mixin`，不碰推理路径。
- **插件职责**：监听事件触发钩子（`event_fire`）做棉签寿命累加/锁定/解锁 + 提供查询接口 + 双视角监控面板。
- **错误隔离**：钩子与接口全程 try/except 兜底，插件异常不影响主程序检测。

---

## 三、目录结构

```
sensor-clean/
├── plugin.json              清单（tier3，capabilities：hooks/routers/alarm/system_config）
├── backend/
│   ├── __init__.py          register_plugin 入口：注入 host + 挂钩子 + 路由
│   ├── hooks.py             耗材约束 + 跨视角联动状态机
│   ├── preset.py            对齐 demo 的项目配置模板
│   └── routes.py            /swab/* 查询与控制接口
└── frontend/
    ├── dist/index.esm.js    双视角监控面板（host 注入风格）
    └── i18n/{zh-CN,en-US}.json
```

---

## 四、配置项

系统配置键 `plugin.sensor-clean.config`（不配则用默认，对齐 demo）：

| 字段 | 默认 | 含义 |
|---|---|---|
| `count_channels` | `[0]` | 视角1 计数通道列表（OK 事件即 +1） |
| `swap_channel` | `1` | 视角2 换棉签通道 |
| `swap_label` | `更换棉签` | 视角2 解锁事件名（匹配事件名/原因） |
| `max_uses_per_swab` | `11` | 一根棉签擦满几个产品后锁定（K） |
| `alarm_event` | `""` | 锁定时触发的报警事件类型（空=不报警） |

改配置后调 `POST /swab/reload-config` 生效。

---

## 五、接口

挂在 `/api/v1/plugins/sensor-clean/swab/` 下：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/state` | 棉签状态：已用 / 上限 / 剩余 / 是否锁定（前端轮询） |
| POST | `/reset` | 手动更换棉签（清零解锁） |
| POST | `/reload-config` | 重新加载插件配置 |
| GET | `/templates` | 取对齐 demo 的项目配置模板 + 耗材默认配置 |

---

## 六、对齐 demo 的项目配置模板

`preset.py` 把 demo 计数参数翻译成主程序「事件计数」字段：

| demo 参数 | 主程序字段 |
|---|---|
| 进入确认 2 帧（ENTER_FRAMES） | `event_min_visible_frames = 2` |
| 离开确认 3 帧（LEAVE_FRAMES） | `event_gone_frames = 3` |
| 一个产品 = 一次 | `event_required_count = 1` |

应用方式：前端面板「查看对齐 demo 的项目配置模板」→ 到「项目」页新建项目导入对应配置。核心检测+计数已对齐；置信度/ROI 等按现场微调。

---

## 七、本地 dev 加载

```bash
export DEBUG_MODE=1
export PLUGIN_DEV_MODE=1
python scripts/plugin/dev-plugin.py --src ./plugins-examples/sensor-clean --activate
# 然后用 tianjun conda 环境启动后端，插件即被加载（dev 模式跳过验签）
```

- 改后端 Python 代码需重启后端；改前端可走 vite 热更新。
- 正式分发走 `pack-plugin.py` 打包 → 主作者 `sign-plugin.py` 签名 → `install-plugin.py` 安装。
