# 福建金龙 — 双工位检测主页定制 (Tier 2)

> 客户专属 UI 插件。覆盖主程序 `monitor.layout.body` slot，把检测主页改成左右半屏双工位 + 右侧 SOP 卡片 + 共用底栏布局。

## 安装包在哪（固定，不要找别的目录）

| 用途 | 路径 |
|---|---|
| **源码（改代码）** | `plugins-examples/fujian-jinlong/` |
| **安装包（设置页上传）** | 仓库根目录 `福建金龙_双工位检测主页-<版本>-internal-demo.tjvplugin` |
| **当前最新** | `福建金龙_双工位检测主页-1.1.6-internal-demo.tjvplugin`（步骤表 step-cell 插件槽桥接） |

安装步骤：设置 → 插件管理 → 上传上述 `.tjvplugin` → 激活 → **硬刷新浏览器**（Ctrl+Shift+R）。

打包命令（发版时）：

```bash
python scripts/plugin/pack-plugin.py --src plugins-examples/fujian-jinlong --out . --skip-build
PLUGIN_KEY_PASSWORD="" python scripts/plugin/sign-plugin.py \
  --in 福建金龙_双工位检测主页-1.1.4-internal-demo-uns.tjvplugin \
  --out . --key dev_keys/plugin_master_pri.pem --secret dev_keys/PLUGIN_SECRET.txt
```

---

## 客户需求 (依据客户手绘草图)

```
┌──────────────────────────────────────────────────────────┐
│                天军科技AI 视觉检测系统                     │
├──────────────────┬──────────────────┬───────────────────┤
│                  │                  │                   │
│   工位 1 视频画面  │   工位 2 视频画面  │   工位 1 SOP 卡片  │
│   (2/5 宽)       │   (2/5 宽)       │   (1/5 宽)        │
│                  │                  │ ────────────────  │
│                  │                  │   工位 2 SOP 卡片  │
│                  │                  │                   │
├──────────────────┼──────────────────┤ ────────────────  │
│ 检测次数  ××     │ 检测次数  ××     │   步骤统计         │
│ OK 次数  ××     │ OK 次数  ××     │   😊 OK 计数      │
│ 合格率   ××%    │ 合格率   ××%    │   😢 NG 计数      │
│ NG 次数  ××     │ NG 次数  ××     │                   │
│ NG 步骤  1. xxx │ NG 步骤  1. xxx │                   │
│         2. xxx │         2. xxx │                   │
├──────────────────┴──────────────────┴───────────────────┤
│          [开始]   [停止]   [待机]   [清零]                │
└──────────────────────────────────────────────────────────┘
```

## 技术要点

- **Tier 2 纯前端插件** — 不动后端，不挂 hook，不写库。
- 注册 `monitor.layout.body` slot 完全覆盖主程序 Monitor 主体。
- 控制按钮通过主程序透传的 `actions` props 调主程序方法（开始/停止/待机/清零），完整复用项目/模型解析 + 扫码闭环等业务逻辑。
- 视频走 `<img :src="mjpeg_url" />` 简化版（v3.13.2 主程序透传 `streamUrlBuilder` helper）。ROI 标注 / Kalman 平滑等 canvas 渲染暂不支持，等真实客户提需求再升级。
- 全部数据来自主程序透传的 `multiChannelData` prop（`total / ok / ng / yieldRate / steps / ngStepRanking / projectName / isRunning / isDetecting`）。

## 配套主程序版本

要求主程序 ≥ **v3.13.2**（layout.body slot 支持 `:actions` / `:streamUrlBuilder` prop 透传）。

## 安装步骤

1. 把整个目录拷到客户机 `<安装目录>/plugins/fujian-jinlong/`
2. 在 Settings → 插件管理 激活 "福建金龙双工位主页"
3. 重启应用
4. 进入 Monitor 页面，确认看到客户专属布局

## 卸载

Settings → 插件管理 → 卸载 → 选"清理插件数据"（如有）→ 重启
