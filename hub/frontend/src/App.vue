<template>
  <!-- key=fullPath: 路由参数变化 (工位A→工位B) 强制重挂载 ——
       StationView 在 setup 一次性读 params, 复用组件会停在旧工位 (状态审计发现) -->
  <router-view :key="$route.fullPath" />
</template>

<style>
/* ============================================================
   全局设计 token —— 与主程序同源 (slate 系 + tech-blue)。
   调研依据 (2026-09 三方调研: ISA-101 HMI / 舰队产品竞品 / 暗色设计系统):
   - 暗色层次靠"提亮表面+发丝边", 阴影只给浮层 (Material3/Linear/Primer)
   - ISA-101: 正常态灰度安静, 红黄专属"需行动"异常; 绿仅作操作回执
   - WCAG: #64748b 仅 placeholder/disabled; 主按钮用深字 (白字 2.6:1 不合格)
   - 数字/时间戳 tabular-nums 防抖动; 中文 letter-spacing 0; 标题 600 不用 700
   ============================================================ */
:root {
  /* 表面四档 (暗色 elevation = 变亮) */
  --hub-bg: #0f172a;            /* surf-0: 页面底/输入井 */
  --hub-panel: #1e293b;         /* surf-1: 卡片/顶栏/表头 */
  --hub-hover: #263449;         /* surf-2: 行 hover/下拉/嵌套 */
  --hub-float: #2d3b50;         /* surf-3: 模态/弹出菜单 */
  --hub-input-bg: #0f172a;
  --hub-border: #334155;        /* 卡片外轮廓/控件边 */
  --hub-border-soft: rgba(148, 163, 184, 0.12); /* 卡内分隔/表行线 */

  /* 文字四阶 (text-4 仅 placeholder/disabled, 不作可读文字) */
  --hub-text: #e2e8f0;
  --hub-text-2: #cbd5e1;
  --hub-text-3: #94a3b8;
  --hub-text-4: #64748b;

  /* 品牌 */
  --hub-primary: #00a8ff;         /* tech-blue: 链接/焦点/主 CTA */
  --hub-primary-hover: #33b8ff;   /* 同色相提亮, 不跳青 */
  --hub-on-primary: #0f172a;      /* 主按钮文字 (深字过 AA) */

  /* 状态 (ISA-101: muted 为常规形态, 实底仅确认框危险按钮/报警横幅) */
  --hub-ok: #34d399;    --hub-ok-bg: #10b98126;    --hub-ok-bd: #10b98140;
  --hub-warn: #fbbf24;  --hub-warn-bg: #f59e0b26;  --hub-warn-bd: #f59e0b40;
  --hub-ng: #f87171;    --hub-ng-bg: #ef444426;    --hub-ng-bd: #ef444440;
  --hub-ng-solid: #ef4444;        /* 实底红: 确认框危险按钮/KPI 异常数字 */

  /* 形状与运动 */
  --hub-radius-sm: 4px;           /* 徽标 */
  --hub-radius: 6px;              /* 按钮/输入 */
  --hub-radius-lg: 10px;          /* 卡片 */
  --hub-ease: cubic-bezier(0.4, 0, 0.2, 1);
}

* { margin: 0; padding: 0; box-sizing: border-box; }
html, body, #app { height: 100%; }
body {
  background: var(--hub-bg);
  color: var(--hub-text-2);
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC",
    ui-sans-serif, sans-serif;
  font-size: 14px;
  line-height: 20px;
  letter-spacing: 0;             /* 中文禁 tracking */
  font-synthesis: none;          /* 禁 faux bold (中文发虚主因) */
  color-scheme: dark;
}
a { color: inherit; text-decoration: none; }

/* 数字/时间戳等宽数字: 计数跳动不挤动版面 */
.tabular {
  font-variant-numeric: tabular-nums lining-nums;
  font-feature-settings: "tnum" 1, "lnum" 1;
}

/* 键盘焦点: 双层环 (WCAG 1.4.11) */
:focus-visible {
  outline: 2px solid var(--hub-primary);
  outline-offset: 2px;
}
.hub-input:focus-visible, .hub-select:focus-visible {
  outline: none;
  border-color: var(--hub-primary);
  box-shadow: 0 0 0 3px rgba(0, 168, 255, 0.25);
}

@media (prefers-reduced-motion: reduce) {
  * { transition-duration: 0ms !important; animation: none !important; }
}

/* ============================================================
   通用组件类
   ============================================================ */

/* 顶栏 */
.hub-topbar {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  min-height: 52px; padding: 12px 20px;
  background: var(--hub-panel); border-bottom: 1px solid var(--hub-border);
  position: sticky; top: 0; z-index: 5;
}
.hub-topbar h1 {
  font-size: 16px; color: var(--hub-text); font-weight: 600;
  white-space: nowrap;
}
.hub-back {
  background: none; border: none; color: var(--hub-primary);
  cursor: pointer; font-size: 14px; white-space: nowrap;
}
.hub-back:hover { color: var(--hub-primary-hover); }

/* 文字链接按钮 */
.hub-link {
  background: none; border: none; color: var(--hub-primary);
  cursor: pointer; font-size: 14px; white-space: nowrap;
}
.hub-link:hover { color: var(--hub-primary-hover); }

/* 按钮: 主=tech-blue 深字 / ghost / danger 梯度 */
.hub-btn {
  padding: 8px 16px; border: 1px solid transparent;
  border-radius: var(--hub-radius);
  background: var(--hub-primary); color: var(--hub-on-primary);
  font-weight: 600;
  cursor: pointer; font-size: 14px; white-space: nowrap;
  transition: background-color 150ms var(--hub-ease),
    border-color 150ms var(--hub-ease), color 150ms var(--hub-ease);
}
.hub-btn:hover:not(:disabled) { background: var(--hub-primary-hover); }
.hub-btn:active:not(:disabled) { background: #0096e6; transition-duration: 0ms; }
.hub-btn:disabled { opacity: .5; cursor: not-allowed; }
.hub-btn.sm { padding: 5px 12px; font-size: 13px; }
.hub-btn.ghost {
  background: transparent; color: var(--hub-text-3);
  border-color: var(--hub-border); font-weight: 400;
}
.hub-btn.ghost:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.06); color: var(--hub-text);
}
.hub-btn.ghost-primary {
  background: transparent; color: var(--hub-primary);
  border-color: var(--hub-primary); font-weight: 400;
}
.hub-btn.ghost-primary:hover:not(:disabled) {
  color: var(--hub-primary-hover); border-color: var(--hub-primary-hover);
  background: rgba(0, 168, 255, 0.08);
}
.hub-btn.ghost-danger {
  background: transparent; color: var(--hub-ng);
  border-color: var(--hub-ng-bd); font-weight: 400;
}
.hub-btn.ghost-danger:hover:not(:disabled) {
  border-color: var(--hub-ng); background: var(--hub-ng-bg);
}
.hub-btn.danger { background: var(--hub-ng-solid); color: #fff; }
.hub-btn.danger:hover:not(:disabled) { background: #dc2626; }

/* 输入 / 下拉 */
.hub-input, .hub-select {
  width: 100%; padding: 9px 12px;
  border-radius: var(--hub-radius); border: 1px solid var(--hub-border);
  background: var(--hub-input-bg); color: var(--hub-text);
  font-size: 14px; outline: none;
  transition: border-color 150ms var(--hub-ease);
}
.hub-input:hover, .hub-select:hover { border-color: #475569; }
.hub-input:focus, .hub-select:focus { border-color: var(--hub-primary); }
.hub-input::placeholder { color: var(--hub-text-4); }
.hub-select {
  appearance: none; cursor: pointer; padding-right: 34px;
  background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2.5 4.5l3.5 3.5 3.5-3.5' fill='none' stroke='%2394a3b8' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 12px center;
}

/* 模态: surf-3 更亮一档 + 浮层专属阴影 */
.hub-modal-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, 0.72);
  display: flex; align-items: center; justify-content: center; z-index: 20;
}
.hub-modal {
  width: 400px; padding: 24px; border-radius: 12px;
  background: var(--hub-float); border: 1px solid var(--hub-border);
  box-shadow: 0 8px 24px rgba(0, 0, 0, .45), 0 0 0 1px rgba(255, 255, 255, .04);
  display: flex; flex-direction: column; gap: 14px;
}
.hub-modal h2 { color: var(--hub-text); font-size: 16px; font-weight: 600; }
.hub-modal label {
  display: flex; flex-direction: column; gap: 6px;
  color: var(--hub-text-3); font-size: 13px;
}
.hub-modal label.row { flex-direction: row; align-items: center; gap: 8px; }
.hub-modal .hub-input, .hub-modal .hub-select { background: var(--hub-bg); }
.hub-modal-actions {
  display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px;
}
.hub-error { color: var(--hub-ng); font-size: 13px; }

/* 表格: 行高 40px, hover 实色提亮, 行线用发丝线 */
.hub-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.hub-table th {
  text-align: left; color: var(--hub-text-3); font-weight: 500;
  font-size: 12px; height: 40px; padding: 0 12px;
  border-bottom: 1px solid var(--hub-border);
  white-space: nowrap;
}
.hub-table td {
  height: 40px; padding: 0 12px;
  border-bottom: 1px solid var(--hub-border-soft);
  color: var(--hub-text-2);
  transition: background-color 150ms var(--hub-ease);
}
.hub-table tbody tr:hover td { background: var(--hub-hover); }

/* 状态徽标: muted 三件套 (低透明底+亮字+淡边), 不用实底色块 */
.hub-badge {
  display: inline-block; padding: 1px 8px;
  border-radius: var(--hub-radius-sm); font-size: 12px;
  border: 1px solid transparent;
}
.hub-badge.ng { background: var(--hub-ng-bg); color: var(--hub-ng); border-color: var(--hub-ng-bd); }
.hub-badge.ok { background: var(--hub-ok-bg); color: var(--hub-ok); border-color: var(--hub-ok-bd); }
.hub-badge.warn { background: var(--hub-warn-bg); color: var(--hub-warn); border-color: var(--hub-warn-bd); }
.hub-badge.neutral { background: transparent; color: var(--hub-text-3); border-color: var(--hub-border); }

/* 状态点: 形状冗余编码 (ISA-101/WCAG 1.4.1 色盲不依赖颜色)
   在线=空心灰环(常态安静) / 滞后=琥珀菱形 / 离线=红方块 */
.dot { display: inline-block; width: 10px; height: 10px; flex-shrink: 0; }
.dot.online {
  border-radius: 50%; border: 2px solid var(--hub-text-3); background: transparent;
}
.dot.stale {
  background: var(--hub-warn); border-radius: 1px; transform: rotate(45deg) scale(.85);
}
.dot.offline { background: var(--hub-ng-solid); border-radius: 1px; }
.dot.unknown {
  border-radius: 1px; border: 2px solid var(--hub-text-4); background: transparent;
}

/* 空态 / 加载失败 */
.hub-empty { color: var(--hub-text-3); text-align: center; padding: 60px 0; }
.hub-load-error { color: var(--hub-ng); text-align: center; padding: 12px 0; }
</style>