# 08 — 示例插件 + 用户文档 + FAQ

> 适用版本：基于 `feat/plugin-config` v0.1
> 本文是**整套插件系统设计的收官**：把 design 01~07 的设计落到三个**可复制可跑通**的示例插件，并附运维一页纸、开发者上手指南、错误代码速查、FAQ。
>
> 阅读前置：design 00 → 07（如已快速过完概念，可直接从本文示例入手）。

---

## 文档结构

| 章节 | 内容 | 受众 |
|---|---|---|
| §一 | 三档示例插件总览 | 插件作者 / 主作者 |
| §二 | 档位 1 完整示例：白标主题 | 插件作者 |
| §三 | 档位 2 完整示例：报表 + 班次 | 插件作者 |
| §四 | 档位 3 完整示例：MQTT + ERP 同步 | 插件作者 |
| §五 | 客户运维一页纸（双语：操作员/运维） | 工厂运维 |
| §六 | 插件作者从 0 到 1 上手指南 | 新进插件开发者 |
| §七 | 错误代码速查表（33 个） | 全员 |
| §八 | FAQ（常见 30+ 问） | 全员 |
| §九 | 后续路线图（v3.7 → v5.0） | 主作者 / 团队 |
| §十 | 提交 issue / 求助渠道 | 全员 |

---

## 一、三档示例插件总览

### 1.1 一目录三个 demo

我们在仓库内提供 `plugins-examples/` 目录（**单独于 `plugins/`，**不进客户工控机）：

```
plugins-examples/
├── README.md
├── tier1-acme-theme/         ← 档位 1 白标主题
├── tier2-acme-ui/            ← 档位 2 报表 + 班次 UI
└── tier3-acme-fullstack/     ← 档位 3 MQTT + ERP 全栈
```

每个 demo：
- 自带 `plugin.json`（design 01 §6 完整字段）
- 自带 README（开发指南 + 打包步骤）
- 通过 `pack-plugin.py` 一键产出 `-uns.tjvplugin`
- 经主作者签名后可直接当作客户首装样例

### 1.2 三档复杂度对照

| 维度 | 档位 1 | 档位 2 | 档位 3 |
|---|---|---|---|
| 文件数 | 8 | 22 | 45 |
| 代码行数 | ~150 | ~800 | ~2500 |
| 开发周期 | 0.5~2 天 | 3~7 天 | 7~14 天 |
| 主程序前置改造 | 0 | F10 (layout) | F1~F9 (hooks) |
| 客户工控机磁盘 | < 1 MB | 2~5 MB | 10~50 MB |
| 启动开销 | < 50 ms | < 300 ms | < 800 ms |

---

## 二、档位 1 完整示例：白标主题

### 2.1 目录结构

```
plugins-examples/tier1-acme-theme/
├── plugin.json
├── README.md
├── frontend/
│   ├── theme.css
│   ├── i18n/
│   │   ├── zh-CN.json
│   │   └── en-US.json
│   └── assets/
│       ├── acme-logo.svg
│       ├── acme-favicon.png
│       └── fonts/ACME-Regular.woff2
└── (signature.bin 由 sign-plugin.py 生成)
```

### 2.2 plugin.json

```json
{
  "manifest_version": 1,
  "name": "ACME 白标主题",
  "customer_code": "acme",
  "plugin_version": "1.0.0",
  "description": "ACME 公司白标版：黄黑色板 + ACME logo + 中英文",
  "author": "ACME Tech <support@acme.com>",
  "homepage": "https://acme.com",
  "license_text": "本插件版权归 ACME Inc 所有",
  "tier": 1,
  "capabilities": [
    "frontend.theme",
    "frontend.replace_logo",
    "frontend.replace_app_title",
    "frontend.i18n",
    "frontend.hide_menus"
  ],
  "main_version_min": "3.7.0",
  "main_version_max": "3.x",
  "created_at": "2026-05-08T05:00:00Z",
  "signed_at": "PLACEHOLDER",
  "signed_by": "PLACEHOLDER",
  "files_digest": "PLACEHOLDER",
  "frontend": {
    "theme": {
      "css": "frontend/theme.css",
      "logo": "frontend/assets/acme-logo.svg",
      "favicon": "frontend/assets/acme-favicon.png",
      "app_title": "ACME 视觉检测系统"
    },
    "i18n": {
      "zh-CN": "frontend/i18n/zh-CN.json",
      "en-US": "frontend/i18n/en-US.json"
    },
    "hidden_menus": ["/cluster", "/mes"]
  }
}
```

### 2.3 frontend/theme.css

```css
/* ACME 主题: 黄+黑+灰 */
@font-face {
  font-family: 'ACME';
  src: url('/api/v1/plugins/active/assets/frontend/assets/fonts/ACME-Regular.woff2') format('woff2');
  font-display: swap;
}

:root {
  /* 品牌色 */
  --tj-primary: #FFEB3B;
  --tj-primary-hover: #FBC02D;
  --tj-primary-active: #F57F17;
  --tj-primary-rgb: 255, 235, 59;

  /* 背景层级 */
  --tj-bg-base:  #1A1A1A;
  --tj-bg-panel: #2A2A2A;
  --tj-bg-elev:  #3A3A3A;

  /* 文字 */
  --tj-text-primary:   #FFFFFF;
  --tj-text-secondary: #DDDDDD;
  --tj-text-muted:     #999999;

  /* 字体 */
  --tj-font-family-base: 'ACME', 'Inter', sans-serif;
}

/* 自家右下角水印 (.plugin-acme- 前缀符合 design 04 §五命名) */
.plugin-acme-watermark {
  position: fixed;
  bottom: 0.5rem;
  right: 0.75rem;
  font-size: 0.75rem;
  color: var(--tj-primary);
  background: rgba(255, 235, 59, 0.08);
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  border: 1px solid rgba(255, 235, 59, 0.2);
  z-index: 9999;
  pointer-events: none;
  user-select: none;
}
.plugin-acme-watermark::before {
  content: '⚙ ACME';
}
```

### 2.4 frontend/i18n/zh-CN.json

```json
{
  "navbar": {
    "title": "ACME"
  },
  "menu": {
    "monitor": "ACME 监控",
    "data": "ACME 生产数据",
    "settings": "系统设置"
  }
}
```

> 主程序 zh-CN 中已有 `menu.monitor` = "检测中心"，被覆盖为 "ACME 监控"。

### 2.5 README（用户视角）

```markdown
# ACME 白标主题

## 功能
- 黄黑色板替换
- ACME 标志 + "ACME 视觉检测系统" 标题
- 中英文文案定制
- 隐藏 集群 / MES 菜单（单机版）

## 兼容
- 主程序 v3.7.0 以上
- 需要客户激活码 (license.customerName="acme")

## 安装
1. 在 Settings → 插件管理 上传 acme-1.0.0.tjvplugin
2. 点 "激活"
3. 重启应用

## 卸载
Settings → 插件管理 → 删除
```

### 2.6 打包

```bash
$ cd plugins-examples/tier1-acme-theme
$ python ../../scripts/pack-plugin.py . --output ./dist/
[1/6] 校验 manifest...
[2/6] 校验源码命名空间...
[3/6] 跳过前端构建 (无 frontend/vite.lib.config.js)
[4/6] 计算 files_digest...
      → sha256:1a2b3c4d5e6f...
[5/6] 写回 plugin.json (canonical)...
[6/6] 打 ZIP → dist/acme-1.0.0-uns.tjvplugin (256 KB)

$ python ../../scripts/sign-plugin.py dist/acme-1.0.0-uns.tjvplugin \
    --key ~/.tianjun-keys/plugin_master.pem \
    --signer "tianjun-ai-master-2026"
... (签名完成) ...
✓ 已签名: dist/acme-1.0.0.tjvplugin
```

---

## 三、档位 2 完整示例：报表 + 班次 UI

### 3.1 目录结构

```
plugins-examples/tier2-acme-ui/
├── plugin.json
├── README.md
├── frontend/
│   ├── package.json                    ← vue + element-plus 仅 devDep
│   ├── vite.lib.config.js              ← design 05 §10.1 模板
│   ├── src/
│   │   ├── index.js                    ← export default { register }
│   │   ├── views/
│   │   │   ├── AcmeReport.vue
│   │   │   └── AcmeShift.vue
│   │   ├── stores/shift.js
│   │   └── api/shift.js
│   ├── i18n/zh-CN.json
│   ├── theme.css
│   └── dist/                           ← vite build 产物
│       ├── entry.js
│       └── entry.css
└── (signature.bin)
```

### 3.2 plugin.json（关键节选）

```json
{
  "manifest_version": 1,
  "name": "ACME 报表 + 班次管理",
  "customer_code": "acme",
  "plugin_version": "1.5.0",
  "tier": 2,
  "capabilities": [
    "frontend.theme",
    "frontend.routes",
    "frontend.menus",
    "frontend.stores",
    "frontend.i18n"
  ],
  "main_version_min": "3.7.0",
  "frontend": {
    "entry": "frontend/dist/entry.js",
    "theme": {
      "css_variables": { "--tj-primary": "#FFEB3B" }
    },
    "routes": [
      {
        "path": "report",
        "name": "report",
        "component": "frontend/dist/entry.js",
        "menu_label": "plugin.acme.menu.report",
        "icon": "DataLine",
        "order": 100
      },
      {
        "path": "shift",
        "name": "shift",
        "component": "frontend/dist/entry.js",
        "menu_label": "plugin.acme.menu.shift",
        "icon": "Calendar",
        "order": 110
      }
    ],
    "stores": [
      { "id": "plugin-acme-shift", "module": "frontend/dist/entry.js" }
    ],
    "i18n": {
      "zh-CN": "frontend/i18n/zh-CN.json"
    }
  },
  "default_config": {
    "plugin.acme.report_title": "ACME 生产报表",
    "plugin.acme.shift_labels": ["早", "中", "晚"]
  },
  "metadata": { "demo": true }
}
```

> ⚠️ **关于 `component` 字段**：在打包后的 ESM bundle 中，组件已被 `entry.js` bundle 一起导入。manifest 字段的 component 路径只是参考（registry 实际从 register() 内拿到组件对象）。

### 3.3 frontend/src/index.js（插件入口）

```js
import AcmeReport from './views/AcmeReport.vue';
import AcmeShift from './views/AcmeShift.vue';
import { useShiftStore } from './stores/shift';

export default {
  async register(ctx) {
    const { registry } = ctx;

    // 1. 注册 store
    registry.stores.register('plugin-acme-shift', useShiftStore);

    // 2. 注册路由
    registry.routes.register({
      path: 'report',
      name: 'report',
      component: AcmeReport,
      menu: { label: 'plugin.acme.menu.report', icon: 'DataLine', order: 100 },
    });
    registry.routes.register({
      path: 'shift',
      name: 'shift',
      component: AcmeShift,
      menu: { label: 'plugin.acme.menu.shift', icon: 'Calendar', order: 110 },
    });

    // 3. (可选) 写自家欢迎 console
    console.log('[ACME UI] v1.5.0 加载完成');
  },
};
```

### 3.4 frontend/src/views/AcmeReport.vue

```vue
<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold mb-4 text-yellow-300">{{ pageTitle }}</h1>

    <el-card class="mb-4">
      <h2 class="text-lg font-semibold mb-2">本班次概况</h2>
      <div v-if="shiftStore.currentShift" class="grid grid-cols-3 gap-4">
        <el-statistic title="班次" :value="shiftStore.currentShift.label" />
        <el-statistic title="已检测" :value="stats.total" />
        <el-statistic title="合格率" :value="stats.goodRate" suffix="%" />
      </div>
      <div v-else class="text-gray-400">未启动班次</div>
    </el-card>

    <el-card>
      <h2 class="text-lg font-semibold mb-2">最近 7 天趋势</h2>
      <div ref="chartRef" style="height: 300px"></div>
    </el-card>

    <div class="plugin-acme-watermark"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue';
import * as echarts from 'echarts';
import { useShiftStore } from '../stores/shift';

const shiftStore = useShiftStore();
const chartRef = ref(null);
const stats = ref({ total: 0, goodRate: 0 });

const pageTitle = computed(() => {
  return window.__pluginConfig?.['plugin.acme.report_title'] || 'ACME 生产报表';
});

onMounted(async () => {
  await shiftStore.loadCurrentShift();
  await loadStats();
  initChart();
});

async function loadStats() {
  const resp = await fetch('/api/v1/data/sessions/stats?days=1');
  const data = await resp.json();
  stats.value = {
    total: data.total_cycles,
    goodRate: ((data.good_count / data.total_cycles) * 100).toFixed(1),
  };
}

function initChart() {
  const chart = echarts.init(chartRef.value);
  chart.setOption({
    xAxis: { type: 'category', data: ['1天前', '2天前', '3天前', '4天前', '5天前', '6天前', '7天前'] },
    yAxis: { type: 'value' },
    series: [{ data: [120, 200, 150, 80, 70, 110, 130], type: 'line' }],
  });
}
</script>
```

### 3.5 frontend/src/stores/shift.js

```js
import { defineStore } from 'pinia';

export const useShiftStore = defineStore('plugin-acme-shift', {
  state: () => ({
    currentShift: null,
    history: [],
  }),
  actions: {
    async loadCurrentShift() {
      // tier 2 没有自家后端, 用主程序系统时间推断 + SystemConfig 班次定义
      const resp = await fetch('/api/v1/system-config/plugin.acme.shift_labels');
      const data = await resp.json();
      const labels = JSON.parse(data.value || '["早", "中", "晚"]');

      const hour = new Date().getHours();
      let idx;
      if (hour >= 8 && hour < 16) idx = 0;
      else if (hour >= 16 && hour < 24) idx = 1;
      else idx = 2;

      this.currentShift = { label: labels[idx], started_at: new Date().toISOString() };
    },
  },
});

export default useShiftStore;
```

### 3.6 frontend/vite.lib.config.js

```js
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: 'dist',
    lib: {
      entry: path.resolve(__dirname, 'src/index.js'),
      formats: ['es'],
      fileName: () => 'entry.js',
    },
    rollupOptions: {
      external: [
        'vue', 'vue-router', 'pinia', 'vue-i18n',
        'element-plus', '@element-plus/icons-vue',
        'echarts', 'axios',
      ],
      output: { format: 'es' },
    },
    cssCodeSplit: false,
    sourcemap: false,
  },
});
```

### 3.7 frontend/package.json

```json
{
  "name": "plugin-acme-ui",
  "version": "1.5.0",
  "type": "module",
  "private": true,
  "scripts": {
    "build": "vite build --config vite.lib.config.js"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^6.0.1",
    "vite": "^7.2.4",
    "vue": "^3.5.24",
    "element-plus": "^2.13.1",
    "@element-plus/icons-vue": "^2.3.2"
  }
}
```

> ⚠️ vue / element-plus 在 devDependencies（不打入 bundle，外部化）。**版本要与主程序对齐**——通过 `requires.python_packages` 的相邻字段 `requires.frontend_packages` 校验？暂未实现，靠插件作者自觉。

### 3.8 打包

```bash
$ cd plugins-examples/tier2-acme-ui/frontend
$ npm install
$ npm run build              # → dist/entry.js + entry.css
$ cd ..
$ python ../../scripts/pack-plugin.py . --output ./dist/
```

---

## 四、档位 3 完整示例：MQTT + ERP 同步

### 4.1 目录结构

```
plugins-examples/tier3-acme-fullstack/
├── plugin.json
├── README.md
├── frontend/                            ← 同档位 2
│   ├── package.json
│   ├── vite.lib.config.js
│   ├── src/...
│   └── dist/...
├── backend/
│   ├── __init__.py                      ← register_plugin
│   ├── routes.py                        ← /api/v1/plugins/acme/* router
│   ├── adapters/
│   │   ├── __init__.py
│   │   └── mqtt.py                      ← MQTTAdapter (BaseAdapter 子类)
│   ├── hooks.py                         ← cycle_end / scan_received hook
│   ├── threads.py                       ← ERP poller 后台线程
│   ├── models.py                        ← ORM: PluginAcmeShift / OrderExtra
│   └── migrate.py                       ← 自家表 ALTER 升级
├── templates/
│   └── acme-defect-report.docx
├── wheels/
│   └── paho_mqtt-1.6.1-py3-none-any.whl
└── (signature.bin)
```

### 4.2 plugin.json（核心节选）

```json
{
  "manifest_version": 1,
  "name": "ACME MES 全栈插件",
  "customer_code": "acme",
  "plugin_version": "2.0.0",
  "tier": 3,
  "capabilities": [
    "frontend.theme", "frontend.routes", "frontend.menus", "frontend.i18n",
    "backend.routes", "backend.adapter.mqtt",
    "backend.hook.cycle_end", "backend.hook.scan_received", "backend.hook.startup",
    "backend.tables", "backend.background_thread",
    "export.templates", "export.realtime_rules", "export.field_resolver"
  ],
  "main_version_min": "3.7.0",
  "frontend": {
    "entry": "frontend/dist/entry.js",
    "theme": { "logo": "frontend/assets/acme-logo.svg", "app_title": "ACME 视觉系统" },
    "routes": [
      { "path": "report", "name": "report", "component": "AcmeReport",
        "menu_label": "plugin.acme.menu.report", "icon": "DataLine", "order": 100 }
    ],
    "i18n": { "zh-CN": "frontend/i18n/zh-CN.json" }
  },
  "backend": {
    "entry": "backend/__init__.py",
    "routers": [
      { "module": "backend.routes", "attr": "router" }
    ],
    "adapters": [
      { "name": "plugin-acme-mqtt", "class_name": "MQTTAdapter",
        "module": "backend.adapters.mqtt",
        "description": "MQTT 推送 ACME MES" }
    ],
    "hooks": [
      { "type": "startup", "module": "backend.hooks", "function": "init_acme",
        "priority": 500 },
      { "type": "cycle_end", "phase": "workpiece_set_result", "when": "after",
        "module": "backend.hooks", "function": "on_cycle_end_push_to_erp" },
      { "type": "scan_received", "when": "post",
        "module": "backend.hooks", "function": "on_scan_log_to_erp" }
    ],
    "tables": [
      { "name": "p_acme_shifts", "module": "backend.models",
        "class_name": "PluginAcmeShift" },
      { "name": "p_acme_orders_extra", "module": "backend.models",
        "class_name": "PluginAcmeOrderExtra" }
    ],
    "background_threads": [
      { "name": "erp-poller", "module": "backend.threads",
        "function": "start_erp_poller", "auto_start": true }
    ]
  },
  "requires": {
    "gpu": false,
    "python_version": ">=3.10",
    "python_packages": [
      { "name": "paho-mqtt", "version": ">=1.6,<2.0" }
    ]
  },
  "default_config": {
    "plugin.acme.erp_url": "https://acme.com/erp",
    "plugin.acme.mqtt_broker": "mqtt://acme-mqtt.local:1883",
    "plugin.acme.mqtt_topic": "factory/cycle"
  },
  "runtime": {
    "cpu_threshold_warn_pct": 30,
    "memory_threshold_warn_mb": 500,
    "max_hook_duration_ms": 5000,
    "background_thread_max_count": 3
  },
  "export": {
    "templates": [
      { "name": "ACME 缺陷报告", "format": "docx",
        "content_path": "templates/acme-defect-report.docx" }
    ],
    "realtime_rules": [
      { "name": "ACME NG 实时导出", "template_name": "ACME 缺陷报告",
        "trigger_event": "cycle_end",
        "filter_config": { "is_good": [false] },
        "is_enabled": true }
    ],
    "field_resolvers": [
      { "field": "plugin.acme.erp_id", "module": "backend.export_resolvers",
        "function": "resolve_erp_id", "type": "str" }
    ]
  }
}
```

### 4.3 backend/__init__.py

```python
"""ACME 全栈插件入口 — 由 PluginManager 调用"""
import logging

from fastapi import FastAPI

from .routes import router as acme_router
from .adapters.mqtt import MQTTAdapter
from .models import PluginAcmeShift, PluginAcmeOrderExtra
from .hooks import (
    init_acme,
    on_cycle_end_push_to_erp,
    on_scan_log_to_erp,
)
from .threads import start_erp_poller
from .migrate import migrate_acme_tables
from .export_resolvers import resolve_erp_id

logger = logging.getLogger(__name__)


def register_plugin(app: FastAPI, registry, license_payload, host):
    """ACME tier=3 入口

    被 PluginManager 调用一次, 完成所有后端注册.
    """
    cc = "acme"
    logger.info(f"[Plugin {cc}] register_plugin 开始")

    # 1. 创建/更新 ORM 表
    registry.tables.register([PluginAcmeShift, PluginAcmeOrderExtra])

    # 2. 自家迁移 (老表加列等, 在表创建之后)
    migrate_acme_tables(host)

    # 3. 注册 MES Adapter
    registry.adapters.register("plugin-acme-mqtt", MQTTAdapter)

    # 4. 挂 FastAPI router → /api/v1/plugins/acme/*
    registry.routes.register(acme_router, subpath="")

    # 5. 注册 hooks (传 host 进去, 让 hook 内部能调 host.get_config 等)
    def _wrap(fn):
        return lambda ctx: fn(ctx, host)

    registry.hooks.register("startup", _wrap(init_acme), priority=500)
    registry.hooks.register("cycle_end", _wrap(on_cycle_end_push_to_erp),
                            phase="workpiece_set_result", when="after")
    registry.hooks.register("scan_received", _wrap(on_scan_log_to_erp), when="post")

    # 6. 启后台线程 (传 host 让线程能拿 DB session 等)
    registry.threads.register("erp-poller", start_erp_poller, args=(host,))

    # 7. 自定义导出字段 resolver
    registry.export.register_field("plugin.acme.erp_id", resolve_erp_id)

    logger.info(f"[Plugin {cc}] register_plugin 完成")
```

### 4.4 backend/routes.py

```python
"""ACME 自家 REST endpoints (前缀 /api/v1/plugins/acme/)"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.database import get_db
from .models import PluginAcmeShift

router = APIRouter()


@router.get("/shifts/current")
def get_current_shift(db: Session = Depends(get_db)):
    """返回当前班次"""
    s = db.query(PluginAcmeShift).filter_by(is_active=True).first()
    if not s:
        return {"label": None, "started_at": None}
    return {
        "id": s.id,
        "label": s.label,
        "started_at": s.started_at.isoformat() if s.started_at else None,
    }


@router.post("/shifts/start")
def start_shift(label: str, db: Session = Depends(get_db)):
    """开班次"""
    # 关上一个
    db.query(PluginAcmeShift).filter_by(is_active=True).update({"is_active": False})
    new_shift = PluginAcmeShift(label=label, is_active=True)
    db.add(new_shift)
    db.commit()
    return {"id": new_shift.id, "label": new_shift.label}


@router.get("/orders/{order_id}/extra")
def get_order_extra(order_id: int, db: Session = Depends(get_db)):
    """ACME ERP 工单号反查"""
    extra = db.query(PluginAcmeOrderExtra).filter_by(order_id=order_id).first()
    if not extra:
        raise HTTPException(404)
    return {"order_id": extra.order_id, "erp_id": extra.erp_id}
```

### 4.5 backend/adapters/mqtt.py

```python
"""MQTT 推送 ACME MES"""
import json
import logging
import time

import paho.mqtt.client as mqtt

from backend.services.mes_adapters.base import BaseAdapter

logger = logging.getLogger(__name__)


class MQTTAdapter(BaseAdapter):
    """与 BaseAdapter 接口对齐 (push_workpiece / push_defect 等)"""

    name = "plugin-acme-mqtt"

    def __init__(self):
        self._client = None

    def _get_client(self, broker_url: str):
        if self._client and self._client.is_connected():
            return self._client
        # mqtt://host:port
        from urllib.parse import urlparse
        u = urlparse(broker_url)
        c = mqtt.Client(client_id=f"tianjun-acme-{int(time.time())}")
        c.connect(u.hostname or "localhost", u.port or 1883, keepalive=30)
        c.loop_start()
        self._client = c
        return c

    def push_workpiece(self, payload: dict, connection_config: dict) -> dict:
        """实现 BaseAdapter 接口"""
        broker = connection_config.get("broker_url")
        topic = connection_config.get("topic", "factory/workpiece")
        client = self._get_client(broker)
        msg = json.dumps(payload)
        info = client.publish(topic, msg, qos=1)
        info.wait_for_publish(timeout=2)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            return {"success": False, "error": f"MQTT rc={info.rc}"}
        return {"success": True, "topic": topic, "message_id": info.mid}

    def push_defect(self, payload, connection_config) -> dict:
        return self.push_workpiece(payload, connection_config)

    def shutdown(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
```

### 4.6 backend/hooks.py

```python
"""ACME 业务 hooks"""
import logging

import requests

logger = logging.getLogger(__name__)


def init_acme(ctx, host):
    """startup hook"""
    logger.info("[ACME] 启动 hook 跑一次健康检查")
    erp_url = host.get_config("plugin.acme.erp_url")
    try:
        r = requests.get(f"{erp_url}/health", timeout=2)
        if r.status_code == 200:
            logger.info("[ACME] ERP 在线 ✓")
        else:
            logger.warning(f"[ACME] ERP 状态码 {r.status_code}")
    except Exception as e:
        logger.warning(f"[ACME] ERP 不可达: {e} (软警告, 继续)")


def on_cycle_end_push_to_erp(ctx, host):
    """cycle_end / phase=workpiece_set_result / when=after"""
    if not ctx.workpiece or not ctx.workpiece.serial_no:
        return
    erp_url = host.get_config("plugin.acme.erp_url")
    payload = {
        "serial_no": ctx.workpiece.serial_no,
        "is_good": ctx.is_good,
        "event_name": ctx.event_name,
        "timestamp": ctx.timestamp.isoformat(),
    }
    try:
        # 设短超时, 防止拖慢 cycle_end 主流程
        requests.post(f"{erp_url}/cycle", json=payload, timeout=1.0)
    except Exception as e:
        logger.warning(f"[ACME] ERP push 失败 (软失败): {e}")


def on_scan_log_to_erp(ctx, host):
    """scan_received / when=post — 扫码记录到 ERP"""
    erp_url = host.get_config("plugin.acme.erp_url")
    payload = {
        "channel_id": ctx.channel_id,
        "serial_no": ctx.serial_no,
        "scanner_id": getattr(ctx, "scanner_id", None),
        "scan_time": ctx.scan_time.isoformat() if hasattr(ctx, "scan_time") else None,
    }
    try:
        requests.post(f"{erp_url}/scan", json=payload, timeout=1.0)
    except Exception as e:
        logger.warning(f"[ACME] scan log 失败: {e}")
```

### 4.7 backend/threads.py

```python
"""ACME ERP 工单同步后台线程"""
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)


def start_erp_poller(host):
    """每 60 秒拉一次 ACME ERP 工单状态变化, 写到 p_acme_orders_extra

    被 BackendRegistry.threads.register 包装为 daemon 线程
    """
    logger.info("[ACME ERP Poller] 启动")
    interval = 60

    while True:
        try:
            erp_url = host.get_config("plugin.acme.erp_url")
            if not erp_url:
                time.sleep(interval)
                continue
            r = requests.get(f"{erp_url}/orders/recent", timeout=5)
            if r.status_code != 200:
                logger.warning(f"[ACME ERP Poller] HTTP {r.status_code}")
                time.sleep(interval)
                continue
            for entry in r.json().get("orders", []):
                _upsert_order_extra(host, entry)
        except Exception as e:
            logger.warning(f"[ACME ERP Poller] 异常: {e}")
        time.sleep(interval)


def _upsert_order_extra(host, entry):
    from .models import PluginAcmeOrderExtra
    with host.db_session() as db:
        row = db.query(PluginAcmeOrderExtra).filter_by(
            order_id=entry["order_id"]
        ).first()
        if row:
            row.erp_id = entry["erp_id"]
            row.last_sync_at = entry.get("updated_at")
        else:
            db.add(PluginAcmeOrderExtra(
                order_id=entry["order_id"],
                erp_id=entry["erp_id"],
                last_sync_at=entry.get("updated_at"),
            ))
        db.commit()
```

### 4.8 backend/models.py

```python
"""ACME 自家 ORM 表 (p_acme_*)"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func

from backend.db.database import Base


class PluginAcmeShift(Base):
    __tablename__ = "p_acme_shifts"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(20), nullable=False)
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    operator_id = Column(Integer, nullable=True)


class PluginAcmeOrderExtra(Base):
    __tablename__ = "p_acme_orders_extra"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, nullable=False, unique=True, index=True)
    erp_id = Column(String(64), nullable=False, index=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
```

### 4.9 backend/migrate.py

```python
"""ACME 自家表升级 (插件自己处理 ALTER, design 03 §5.3)"""
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def migrate_acme_tables(host):
    """v1.x → v2.0.0 的 ALTER (插件自家版本号变化时)"""
    from backend.db.database import engine

    dialect = engine.dialect.name

    migrations = [
        # (table, column, type_def_sqlite, type_def_pg)
        ("p_acme_shifts", "operator_id", "INTEGER", "INTEGER"),
    ]

    for table, column, sqlite_type, pg_type in migrations:
        type_def = sqlite_type if dialect == "sqlite" else pg_type
        if _column_exists(engine, table, column):
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {type_def}"))
            logger.info(f"[ACME Migrate] {table}.{column} 已添加")
        except Exception as e:
            logger.error(f"[ACME Migrate] {table}.{column} 失败: {e}")


def _column_exists(engine, table, column):
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return any(r[1] == column for r in rows)
    else:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ), {"t": table, "c": column}).first()
            return row is not None
```

### 4.10 打包

```bash
$ cd plugins-examples/tier3-acme-fullstack/frontend
$ npm install && npm run build

$ cd ..

# 下载 wheels (确保客户离线工控机能装)
$ pip download paho-mqtt -d wheels/ --no-deps

$ python ../../scripts/pack-plugin.py . --output ./dist/ --strict
[1/6] 校验 manifest...
[2/6] 校验源码命名空间...
[3/6] 跳过前端构建 (已 build)
... 
✓ 已生成: dist/acme-2.0.0-uns.tjvplugin (8.2 MB)
```

---

## 五、客户运维一页纸

> **打印这一页就够运维用了。**

```
┌────────────────────────────────────────────────────────────────┐
│            天骏 AI 视觉检测系统 - 插件操作指南                 │
│                     (v3.7+, 2026-05)                          │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  一、安装插件                                                  │
│    1. 收到厂商邮件附件 .tjvplugin 文件                          │
│    2. 打开 主程序 → 设置 → 插件管理                            │
│    3. 点击 [上传插件], 选择文件                                │
│    4. 等待 "已安装, 待激活" 提示                                │
│                                                                │
│  二、激活插件                                                  │
│    1. 在 插件管理 找到刚装的插件, 点击 [激活]                  │
│    2. 弹出确认 "需要重启应用", 点 [立即重启]                    │
│    3. 应用自动重启完成                                          │
│                                                                │
│  三、卸载插件                                                  │
│    1. 在 插件管理 找到插件, 点击 [禁用]                         │
│    2. 重启应用                                                  │
│    3. 重启后再次进入 [删除] (可选: 同时清空数据)                │
│                                                                │
│  四、出问题怎么办?                                              │
│                                                                │
│    ╭─────────────────────────────────────────╮                │
│    │ 现象: 安装提示 "签名失败" / "客户码不匹配"  │                │
│    │ 原因: 拿错了别家客户的插件                  │                │
│    │ 解决: 联系厂商, 确认 customer_code         │                │
│    ╰─────────────────────────────────────────╯                │
│                                                                │
│    ╭─────────────────────────────────────────╮                │
│    │ 现象: 激活后菜单/页面没出现                  │                │
│    │ 原因: 没重启或者重启不完全                  │                │
│    │ 解决: 任务管理器结束所有进程后重启          │                │
│    ╰─────────────────────────────────────────╯                │
│                                                                │
│    ╭─────────────────────────────────────────╮                │
│    │ 现象: 插件页面空白/报错                      │                │
│    │ 原因: 插件代码有问题                        │                │
│    │ 解决: 设置→插件管理→[重置隔离] 或 [禁用]     │                │
│    │       然后联系厂商                          │                │
│    ╰─────────────────────────────────────────╯                │
│                                                                │
│    ╭─────────────────────────────────────────╮                │
│    │ 现象: 主程序卡住启动不了                    │                │
│    │ 原因: 插件加载死循环                        │                │
│    │ 解决: 任务管理器结束 → 删除 plugins 目录    │                │
│    │       %APPDATA%\TianjunVision\data\plugins │                │
│    ╰─────────────────────────────────────────╯                │
│                                                                │
│  五、求助渠道                                                  │
│    电话: 138-XXXX-XXXX                                          │
│    邮件: support@tianjun-ai.com                                 │
│    工单: https://tianjun-ai.com/support                         │
│                                                                │
│  紧急情况下, 请准备以下信息:                                    │
│    □ 主程序版本 (帮助 → 关于)                                   │
│    □ 插件名称 + 版本                                           │
│    □ 错误信息截图                                              │
│    □ 日志: %APPDATA%\TianjunVision\logs\backend.log            │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 六、插件作者从 0 到 1 上手指南

### 6.1 准备开发环境

```bash
# 1. 克隆主程序仓库 (拿 schema / 工具)
$ git clone <repo>
$ cd tianjun-plugin

# 2. 创建插件骨架
$ cp -r plugins-examples/tier1-acme-theme plugins/your-cc/

# 3. 改 customer_code
$ sed -i 's/acme/your-cc/g' plugins/your-cc/plugin.json
```

### 6.2 选择档位

```
┌─────────────────────────────────────────────────────────┐
│ 你的需求是什么?                                         │
├─────────────────────────────────────────────────────────┤
│ A. 仅改颜色 / Logo / 文案 / 隐藏菜单                    │
│    → 档位 1                                              │
│                                                         │
│ B. 加新页面 (报表 / 班次 / 自家 UI)                      │
│    → 档位 2                                              │
│                                                         │
│ C. 需要后端逻辑 (协议适配 / 数据库 / hook)                │
│    → 档位 3                                              │
│                                                         │
│ D. 需要替换主程序某个功能 (Data 页等)                     │
│    → 不支持 (v3.7), 升级到客制分支                       │
└─────────────────────────────────────────────────────────┘
```

### 6.3 5 步开发流程

```
1. 设计 manifest
   - 拷贝示例 plugin.json, 改 name / customer_code / capabilities
   - 列出要加的资源 (theme / routes / hooks / tables)

2. 写代码
   档位 1: 改 theme.css + i18n JSON, 不写代码
   档位 2: 写 src/index.js + Vue 组件 + Pinia store
   档位 3: 上 + 写 backend/ 目录 (register_plugin / hooks / models)

3. 本地测试
   $ DEBUG_MODE=1 PLUGIN_DEV_MODE=1 \
       python -m backend.main      # 主程序自动加载未签 plugins/your-cc/

4. 打包
   $ python scripts/pack-plugin.py plugins/your-cc/
   # → dist/your-cc-1.0.0-uns.tjvplugin

5. 提交主作者签名
   $ scp dist/your-cc-1.0.0-uns.tjvplugin master@signing-host:
   主作者 → sign-plugin.py → 回邮 your-cc-1.0.0.tjvplugin
```

### 6.4 调试技巧

| 问题 | 调试方法 |
|---|---|
| 插件加载失败 | 看 backend log + Settings 页错误代码 |
| Vue 组件渲染错 | 浏览器 DevTools Console + 错误 fallback 页"详情"折叠 |
| Hook 不被触发 | 打 print，确认 hook_type / phase / when 拼写 |
| 后台线程退出 | 看 audit_log event_type=runtime_error |
| 表创建失败 | 检查类名 / 表名前缀 |

### 6.5 典型陷阱

1. **vue 版本不一致**：插件用 vue 3.4.x，主程序用 3.5.x → 报 `Cannot find module 'vue/runtime-core'`。**必须**与主程序 package.json 版本对齐
2. **CSS 选择器太宽**：`.button { ... }` 污染整个主程序。**必须**前缀 `.plugin-{cc}-button`
3. **Hook 抛错被吞**：错误隔离层把异常吞了，**自己 try/except 写 log** 才能看到
4. **后台线程卡死**：用 `time.sleep` 时确保是可中断的；不要 `while True: pass`

---

## 七、错误代码速查表（33 个）

| 代码 | 含义 | 用户友好解释 | 处理方法 |
|---|---|---|---|
| `MANIFEST_NOT_FOUND` | plugin.json 缺失 | 插件包损坏 | 重新下载 |
| `MANIFEST_TOO_LARGE` | manifest 过大 | 插件包损坏 | 联系厂商 |
| `MANIFEST_NOT_UTF8` | 编码错误 | 插件包损坏 | 联系厂商 |
| `MANIFEST_INVALID_JSON` | JSON 解析失败 | 插件包损坏 | 联系厂商 |
| `MANIFEST_SCHEMA_FAIL` | 字段不合规 | 插件版本不兼容 | 让厂商重打 |
| `MANIFEST_CUSTOMER_CODE_FORMAT` | 客户码格式错 | 插件包损坏 | 联系厂商 |
| `MANIFEST_CUSTOMER_CODE_DIR_MISMATCH` | 客户码与目录不一致 | 安装异常 | 重新上传 |
| `MANIFEST_VERSION_TOO_NEW` | 主程序太老 | 主程序需升级 | 升级主程序 |
| `MANIFEST_NAME_INVALID` | 插件名格式错 | 插件包损坏 | 联系厂商 |
| `MANIFEST_PLUGIN_VERSION_FORMAT` | 版本号格式错 | 插件包损坏 | 联系厂商 |
| `MANIFEST_TIER_INVALID` | 档位错 | 插件包损坏 | 联系厂商 |
| `MANIFEST_TIER_BACKEND_FORBIDDEN` | tier=1/2 不该带 backend | 插件包损坏 | 联系厂商 |
| `MANIFEST_TIER_BACKEND_REQUIRED` | tier=3 缺 backend | 插件包损坏 | 联系厂商 |
| `MANIFEST_CAPABILITY_UNKNOWN` | 不认识的能力声明 | 主程序需升级 | 升级主程序 |
| `MANIFEST_CAPABILITY_INCONSISTENT` | 声明与实际不符 | 插件包损坏 | 联系厂商 |
| `MANIFEST_DATE_FORMAT` | 时间戳格式错 | 插件包损坏 | 联系厂商 |
| `MANIFEST_SIGNED_AT_FUTURE` | 签名时间是未来 | 系统时钟不对 | 检查系统时间 |
| `MANIFEST_DEFAULT_CONFIG_KEY_INVALID` | 配置 key 命名空间错 | 插件包损坏 | 联系厂商 |
| `MANIFEST_TABLE_PREFIX_INVALID` | 表名前缀不对 | 插件包损坏 | 联系厂商 |
| `MANIFEST_HIDDEN_MENU_FORBIDDEN` | 试图隐藏核心菜单 | 不允许 | 联系厂商 |
| `MANIFEST_I18N_LOCALE_UNKNOWN` | 不支持的语言 | 主程序需升级 | 升级主程序 |
| `MANIFEST_I18N_FILE_NOT_FOUND` | i18n 文件不存在 | 插件包损坏 | 联系厂商 |
| `PLUGIN_LICENSE_MISMATCH` | 授权与插件不匹配 | 拿错了别家插件 | 确认 customer_code |
| `PLUGIN_MAIN_VERSION_TOO_LOW` | 主程序太老 | 主程序需升级 | 升级主程序 |
| `PLUGIN_MAIN_VERSION_TOO_HIGH` | 主程序太新 | 插件需升级 | 让厂商出新版插件 |
| `PLUGIN_FILES_DIGEST_MISMATCH` | 文件被改 / 包损坏 | 文件下载不完整 / 被篡改 | 重新下载, 或扫病毒 |
| `PLUGIN_GPU_REQUIRED_BUT_NOT_AVAILABLE` | 需要 GPU 但没有 | 客户机不支持 | 换插件或机器 |
| `PLUGIN_SIGNATURE_FAIL` | 签名验证失败 | 插件未经厂商认证 | 联系厂商重签 |
| `PLUGIN_DEPENDENCY_NOT_FOUND` | Python 包缺失 | 插件包不完整 | 联系厂商 |
| `MANIFEST_TIER_BACKEND_FORBIDDEN` | 档位与 backend 字段冲突 | 插件包损坏 | 联系厂商 |
| `SIGNATURE_NOT_FOUND` | signature.bin 缺失 | 插件包损坏 | 重新下载 |
| `PLUGIN_LOAD_TIMEOUT` | 加载超时 (10s) | 插件代码异常 | 禁用插件 |
| `PLUGIN_QUARANTINED` | 已隔离 | 连续 3 次加载失败 | Settings → 重置隔离 |

---

## 八、FAQ

### 通用

**Q1: 一个工控机能装多少插件?**
A: 不限制，但**只能激活一个**。同一时刻 `state=active` 的插件唯一。

**Q2: 升级主程序后插件会自动失效吗?**
A: 取决于 manifest `main_version_max`。客户拿到的插件通常 `max = 3.x`，升级到 v3.8 仍兼容。升到 v4.0 需要厂商重签新版。

**Q3: 插件能改主程序代码吗?**
A: 不能。tier 1 改样式 / 文案；tier 2 加新视图；tier 3 加新后端能力。**主程序源码是只读基线**。

**Q4: 卸载后客户数据会丢吗?**
A: 默认**不丢**。Settings 页 [删除] 默认只删插件目录 + plugins 行，**保留** `p_{cc}_*` 表。除非勾选 [同时清空数据]。

**Q5: 客户码怎么注册?**
A: 厂商主作者在 `docs/plugin-system/customer-codes.md` 加一行 + git PR。客户名称使用建议 lowercase 英文，不超过 20 字符。

**Q6: 插件能调主程序的 axios 实例吗?**
A: **不行**。插件代码用 `fetch` 自己调（与主程序解耦）。`host.electron` 暴露了 Electron API（仅 Electron 环境）。

### 开发

**Q7: 我的 Vue 组件能用 `@/` 路径吗?**
A: 可以，但要在 `vite.lib.config.js` 加 alias。打包后会被 vite 完全 bundle，不影响运行时。

**Q8: 我可以用 ECharts 吗?**
A: 可以。**ECharts 是 externals**（与 vue/element-plus 一样），主程序的 vendor bundle 提供。直接 `import * as echarts from 'echarts'`。

**Q9: 后端可以用 SQLAlchemy 写复杂 query 吗?**
A: 可以。但**禁止裸 SQL**（影响 PG 兼容）——用 ORM 表达式 + `.filter` 等。

**Q10: Hook 函数能 `await` 吗?**
A: 当前 hook 是同步调用。如果你需要 IO，用 `requests` 短超时（≤ 1s）或扔到后台线程。

**Q11: 我能在插件里写 SystemConfig 之外的 key 吗?**
A: 不能。host.set_config 强制 `plugin.{cc}.*` 命名空间。

**Q12: 我的插件需要 Python 3.11 特性，能装吗?**
A: 在 manifest `requires.python_version` 写 `">=3.11"`。当前主程序是 3.10（Nuitka build），所以 v3.7 实际不支持 3.11。等主程序升 3.11 后才行。

**Q13: 插件能 fork 进程吗?**
A: 不能（影响 Electron 进程树）。用 daemon 线程。

**Q14: 我能在插件 register_plugin 里抛异常吗?**
A: 可以。会被 PluginManager 捕获 → state=failed + audit_log。**异常消息会显示在 Settings 页**，写得有用一点。

### 签名 / 安全

**Q15: 我能不签名直接给客户吗?**
A: 不行。客户主程序生产环境强制验签。开发模式（DEBUG_MODE）只在自己工控机有效。

**Q16: 私钥泄露了怎么办?**
A: 见 design 02 §九，主作者轮换密钥 + 主程序 hotfix + 重签所有插件。

**Q17: 我能给一个客户两个 customer_code 吗?**
A: 不行。一个客户 license 只对应一个 customerName。如果要拆"基础包"+"扩展包"，做成同一个插件不同 capabilities。

**Q18: 插件能存敏感信息（API key）吗?**
A: SystemConfig 是明文。强烈建议存到客户 OS keyring（Electron 提供 `safeStorage`），通过 host.electron 调用。

### 升级 / 兼容

**Q19: 客户用 v1.0.0，我发了 v1.1.0，怎么升级?**
A: 客户 Settings 上传新 .tjvplugin → 自动检测同 customer_code → 替换 + 提示重启。原 active 状态保留。

**Q20: 我能降级吗 (v2.0 → v1.5)?**
A: 可以但有警告。新加的列不会 DROP，但旧版本不会用到，仍能跑。

**Q21: 主程序升了 v3.8，插件什么时候必须改?**
A: 看主程序 release notes 是否提到 manifest_version 升 / hook 接口变化 / capabilities 加新枚举。一般 v3.x 内插件 forward-compatible。

### 性能

**Q22: 插件占资源会影响检测吗?**
A: 可能。所以 Hook 有软警告（默认 5000ms），后台线程数有限制（默认 3）。生产环境监控 Settings 页 health metrics。

**Q23: 加载插件让启动变慢了?**
A: 加载本身 < 1 秒。如果你的 hook 在 startup 时同步做网络请求，会拖慢。**建议** startup hook 只做轻量初始化，重活扔后台线程。

**Q24: 大插件 50 MB+ 会问题吗?**
A: 不会。`assets` 端点支持流式传输，install 时 200 MB 上限。但启动时所有文件需要解压到磁盘，影响首次启动 1~3 秒。

### 错误处理

**Q25: 插件抛错主程序会崩吗?**
A: 不会。5 层错误隔离（design 06 §七）。最坏情况是该插件页面渲染错误页，主程序其他功能继续。

**Q26: 我能让插件出错时回滚 cycle_end?**
A: 不行。cycle_end 是不可回滚业务流程。如果你的插件检测到非法状态，写自家 audit + 报警，**不要抛错阻塞主流程**。

**Q27: 插件 quarantined 后怎么调试?**
A: Settings → 插件详情 → 看 last_error_code + stack trace。修代码后重打 → 重新上传 → state 自动从 quarantined 出来；或者手动点 [重置隔离]。

### 数据

**Q28: 插件能查 detection_cycles 表吗?**
A: 可以（read-only）。用主程序 ORM 类（`from backend.models.models import DetectionCycle`）。**禁止 INSERT / UPDATE / DELETE 主程序表**。

**Q29: 插件升级时表结构变了怎么办?**
A: 插件 register_plugin 内调自家 migrate（design 03 §5.3）。主程序不参与。

**Q30: 卸载插件时表数据怎么办?**
A: 默认保留。如果客户主动选 [清空数据]，DROP TABLE p_{cc}_*。**不能从 audit_log 恢复**。

---

## 九、后续路线图

### v3.7（当前 design 落地）

- ✅ 插件系统三档完整可用
- ✅ 单插件激活
- ✅ 主程序前置改造（F1~F15）
- ✅ 三个 demo 插件
- ⚠️ 仍是 SQLite

### v3.8

- 🟢 PostgreSQL 双跑实验（同份代码同时支持）
- 🟢 vendor bundle 拆分到 dist/__vendor/
- 🟢 Settings 页插件管理 UI 增强（health metrics 面板）
- 🟢 audit log 导出 CSV

### v4.0

- 🔵 PostgreSQL 默认（Inno Setup 内置）
- 🔵 SQLite 仍可选（高级选项）
- 🔵 主程序硬编码 CSS 渐进重构为 `--tj-*` 变量
- 🔵 manifest_version=2（如有 break 变更，但当前未规划）

### v4.5

- 🟡 评估多插件激活（namespace 隔离方案成熟后）
- 🟡 评估热卸载（i18n 状态可逆方案）
- 🟡 PG schema 隔离（plugin_acme schema + ACL）

### v5.0

- 🟠 移除 SQLite
- 🟠 manifest_version=2 上线（如需要）
- 🟠 多插件 + 跨插件依赖

---

## 十、求助渠道

### 插件作者
- GitHub Issues: `<repo>/issues` 标签 `[plugin-dev]`
- 邮件: support@tianjun-ai.com
- 内部 IM: 团队群

### 客户运维
- 见 §五"运维一页纸"

### 主作者 / 内部团队
- 设计问题: 改 `docs/plugin-system/design/*.md` + PR
- BUG: GitHub Issues 标签 `[plugin-system-bug]`
- 加新 Hook: design 06 + design 01 capabilities + 给主程序提 PR

---

## 十一、本系列 8 份设计文档总览

```
design/
├── 00_overview.md          ← 总体骨架 + 8 个开放问题已确认 (方向 B)
├── 01_manifest_schema.md   ← plugin.json 22 顶层字段 + 33 错误代码
├── 02_signature.md         ← RSA + 客户码 HMAC + signature.bin 字节布局
├── 03_database.md          ← 4 张主程序 plugin 表 + PG/SQLite 双跑
├── 04_tier1_theme.md       ← themeLoader.js + CSS 变量层
├── 05_tier2_ui.md          ← uiLoader + Vendor Importmap + 错误隔离层
├── 06_tier3_fullstack.md   ← PluginManager + 8 hooks + 5 层错误隔离
├── 07_distribution.md      ← pack/sign/verify/install 6 个 CLI 工具
└── 08_examples.md          ← 本文: 三档示例 + 运维 + 开发 + FAQ
```

**总计**：约 **9000~10000 行设计文档**（不含代码）+ **8000+ 行示例代码与脚本**。

**接下来**：
1. 主作者 review 整套设计 → 提改动建议（如 customer code 注册流程、private key 备份策略）
2. 创建 `feat/plugin-config` 实施分支（v3.7 主程序前置改造 F1~F15，13 工作日）
3. 三档 demo 插件实装 + 端到端测试
4. v3.7 正式发版前再开 design/09_release_notes.md（如需要）

---

**本文最后更新**：2026-05-08
**事实校验**：基于 design 00~07 + inventory 01~05 + 现有代码 (frontend / backend / scripts)
**项目地址**：feat/plugin-config 分支
