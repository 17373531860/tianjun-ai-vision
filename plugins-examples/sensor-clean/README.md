# 传感器清洁工序插件（sensor-clean）

> Tier 3 全栈插件 · customer_code = `sensor-clean`
>
> 把「传感器清洁」demo 的双视角检测 + 棉签寿命约束搬进天军主程序。**检测推理复用主程序引擎；产品计数由插件挂帧级钩子按 demo 的 ProductCounter 算法逐帧自计算**，逐件复刻 demo 计数精度。

---

## 一、业务背景

来源于 demo（`detect（视角1用）.py`）：

- **视角1**：检测「查看产品有无脏污 / 擦拭产品」两个动作。锚动作（查看脏污）出现→稳定→离开镜头，即计一件。
- **视角2**：检测「更换棉签」动作。
- **耗材约束**：一根棉签擦满 **K** 个产品（demo 默认 11）后必须更换，否则锁定计数；视角2 检出换棉签后自动解锁清零。

---

## 二、架构（帧级复刻式）

```
主程序（推理引擎，不改业务逻辑）            本插件（挂帧级钩子）
┌────────────────────────────┐          ┌──────────────────────────┐
│ 视角1 通道：YOLO 逐帧推理     │ 每帧检测框 │ detection_frame 钩子      │
│   查看脏污 / 擦拭产品         │ ───────► │  ProductCounter 逐帧计数  │
│                            │          │  → 离开计 1 件 + 棉签 +1   │
│ 视角2 通道：YOLO 逐帧推理     │ 每帧检测框 │  → 达 K 锁定 + 报警提示    │
│   更换棉签                  │ ───────► │  → 视角2 换棉签 → 解锁清零 │
└────────────────────────────┘          └──────────────────────────┘
                                          + 双视角监控面板（前端）
                                          + 一键应用对齐 demo 的默认配置
```

- **检测**：复用主程序引擎（多通道 + 模型加载 + YOLO 推理），插件不写任何 `source_*_mixin`、不碰推理实现。
- **计数**：由插件接管。主程序推理循环每帧把检测框通过 `detection_frame` 钩子广播出来，插件用 demo 同款 ProductCounter 算法逐帧跑锚动作生命周期，离开即计一件。**这样能逐件复刻 demo 计数精度**（主程序的周期结算机制与 demo 不同，无法逐件对齐，故由插件自计数）。
- **错误隔离**：钩子与接口全程 try/except 兜底，插件异常不影响主程序检测。

### 对未启用本插件的主程序：零影响

- `detection_frame` 是 observe-only 钩子，返回值不在可写白名单、会被丢弃，**不能改主程序检测/计数/录像/状态机**。
- 没有 active 插件时，钩子分发在「插件注册表为空」处立即返回，根本不进入插件代码；每帧仅多出微秒级的空判断开销，对帧率无实质影响（与主程序既有的步骤计时广播钩子同款写法）。
- 跟踪（tracking）模式不触发本钩子。

---

## 三、目录结构

```
sensor-clean/
├── plugin.json              清单（tier3，capabilities：hooks/routers/alarm/system_config_write）
├── backend/
│   ├── __init__.py          register_plugin 入口：注入 host + 挂 detection_frame 钩子 + 路由
│   ├── hooks.py             耗材约束 + 跨视角联动状态机（逐帧）
│   ├── counter.py           demo 同款 ProductCounter + 换棉签稳定窗口（归一化坐标）
│   ├── preset.py            对齐 demo 的默认参数 + 双视角项目/模型模板
│   └── routes.py            /swab/* 查询与控制接口
└── frontend/
    ├── dist/index.esm.js    双视角监控面板（host 注入风格）
    └── i18n/{zh-CN,en-US}.json
```

---

## 四、配置项

系统配置键 `plugin_sensor_clean_config`（不配则用 demo 实测最优默认）：

| 字段 | 默认 | 含义 |
|---|---|---|
| `count_channels` | `[0]` | 视角1 计数通道列表 |
| `count_anchor_label` | `查看产品有无脏污` | 视角1 锚动作标签（demo cls0） |
| `swap_channel` | `1` | 视角2 换棉签通道 |
| `swap_label` | `更换棉签` | 视角2 换棉签动作标签 |
| `max_uses_per_swab` | `11` | 一根棉签擦满几个产品后锁定（K） |
| `alarm_event` | `""` | 锁定时触发的报警事件类型（空=不报警） |
| `move_threshold` | `0.0116` | 移动阈值（归一化 = demo 20px / 1728 宽） |
| `lock_spatial` | `0.0203` | 位置锁（归一化 = demo 35px / 1728 宽） |
| `lock_time` | `2.0` | 时间锁 / 计数冷却（秒） |
| `enter_frames` | `2` | 进入确认帧数 |
| `leave_frames` | `3` | 离开确认帧数 |
| `disappear_tolerance` | `20` | 短暂消失容忍帧数（抗实时推理丢帧导致的重复计数） |

> `move_threshold` / `lock_spatial` 是归一化阈值（= demo 像素阈值 / 视频宽）。换了不同分辨率的视频/模型，按此换算重设；`disappear_tolerance` 按现场实时丢帧率调（demo 离线=0，本机实时实测=20）。

改配置后调 `POST /swab/reload-config` 生效（一键应用端点会自动热加载）。

---

## 五、接口

挂在 `/api/v1/plugins/sensor-clean/swab/` 下：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/state` | 棉签状态：已用 / 上限 / 剩余 / 是否锁定（前端轮询） |
| POST | `/reset` | 手动更换棉签（清零解锁） |
| POST | `/reload-config` | 重新加载插件配置 |
| GET | `/templates` | 取对齐 demo 的项目/模型模板 + 计数默认配置 |
| POST | `/apply-preset` | **一键应用**：把对齐 demo 的最优计数/耗材参数写库持久化 + 热加载，并返回视角1/视角2 的项目+模型配置 |

---

## 六、一键应用 + 项目配置

插件激活后，计数/耗材参数即为 demo 实测最优默认值（见第四节），无需手工配置。前端面板点「一键应用 demo 默认配置」会：

1. 把最优参数写入系统配置并热加载（持久化，可后续再改）；
2. 返回视角1/视角2 的项目名 + 默认模型路径，提示你到「项目」页用这两个模型新建项目，分别激活到视角1 / 视角2 通道。

> ⚠️ `preset.py` 里的默认模型路径指向开发机的 demo 素材目录，**客户部署时必须改成现场自己的权重路径**（在「项目」页指定，或改 `preset.py` 的 `_DEMO_DIR`）。

项目侧只需让模型识别出对应动作标签即可（无序检测模式，置信度对齐 demo 的 0.7）；**计数精度全在插件侧的逐帧参数里**，与主程序的周期结算无关。

### 一致性验证

- demo 离线基准（best.pt + test.mp4，1728×1080）：**35 件**
- 主程序 + 本插件实时真跑同一视频：**34 件**（差 1 件为实时推理丢帧的物理极限，已通过 `disappear_tolerance` 压到最优）

---

## 七、本地 dev 加载

```bash
export DEBUG_MODE=1
export PLUGIN_DEV_MODE=1
python scripts/plugin/dev-plugin.py --src ./plugins-examples/sensor-clean --activate
# 然后用 tianjun conda 环境启动后端，插件即被加载（dev 模式跳过验签）
```

- 改后端 Python 代码需重启后端；改前端面板（手写 `dist/index.esm.js`）刷新页面即可。
- 正式分发：打包成 `.tjvplugin`（ZIP）→ 主作者用私钥签名 → 客户在「设置」页上传安装。本仓已提供未签名包 `dist/sensor-clean-1.0.0-uns.tjvplugin`，仅供 dev/本地测试，**生产交付需补签名**。
