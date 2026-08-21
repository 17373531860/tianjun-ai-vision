/**
 * v3.54 检测主页自定义布局 — 运行时核心（模块级单例）
 *
 * 机制（"样式接管"而非组件重排）：
 * - 模板里给形态根容器打 data-layout-canvas="<形态族>"，给区块打 data-layout-slot="<slot id>"。
 *   无自定义布局时这些属性完全惰性，现有 flex/grid 布局零差异（老客户升级无感）。
 * - 某形态存在自定义布局（或处于编辑态）时，本模块把该形态画布内的全部 slot 元素
 *   绝对定位到画布百分比坐标；画布 = 最近的 data-layout-canvas 元素
 *   （单工位/放大态=整页根；双/三工位=工位列，一套列内布局镜像应用到每一列）。
 * - 原 inline style 在接管前暂存，恢复默认/布局删除时原样还原 —— 布局系统可整体退场。
 *
 * 升级安全（reconcile）：
 * - 布局里认识的 slot 用自定义位置；新版本新增的 slot（布局里没有）落到左下角
 *   兜底区可见可再编辑，绝不丢失；布局里有、页面已不存在的 slot 静默忽略。
 * - 任何布局数据异常整体回默认渲染，检测主页永不因布局数据挂掉。
 *
 * 形态键（form_key）：
 * - single:<mode>（mode = default|tracking|weighing|per_item，随激活项目逻辑模式）
 * - dual / triple / grid / zoom（放大态与多显示器 kiosk 共用 zoom）
 * - 画布属性只写"形态族"（single/dual/.../zoom），完整键由 Monitor 侧计算。
 */
import { ref, computed, watchEffect } from 'vue';
import { dbg, dbgErr } from '@/utils/debug';
import {
  getMonitorLayouts, putMonitorLayout,
  deleteMonitorLayout, deleteAllMonitorLayouts,
} from '@/api/monitorLayout';

export const LAYOUT_VERSION = 1;
// 新增 slot 的兜底落点（升级 reconcile / 布局缺项时），依次错开摆放
const FALLBACK_RECT = { x: 0.02, y: 0.66, w: 0.3, h: 0.3 };
const DRAFT_LS_PREFIX = 'tj_layout_draft::';

// ---------------- 模块级状态 ----------------
const layouts = ref({});          // { form_key: layout }（后端已保存）
const loaded = ref(false);
const editMode = ref(false);
const editFormKey = ref(null);
const draft = ref(null);          // 编辑中的布局（保存前不落后端）
const draftRev = ref(0);          // 拖拽中细粒度变更通知（draft 深层变更计数）
const selectedSlot = ref(null);   // 编辑器当前选中 slot id

let rootEl = null;                // Monitor 页根（applier 扫描范围）
let getFormKeyFn = null;          // Monitor 注入的"当前形态键"计算函数
let observer = null;
let applyScheduled = false;

// ---------------- 布局数据加载 ----------------
export async function loadLayouts() {
  try {
    const res = await getMonitorLayouts();
    layouts.value = res.data?.layouts || {};
    loaded.value = true;
    dbg('monitor', '自定义布局已加载', `${Object.keys(layouts.value).length} 个形态`);
  } catch (e) {
    // 加载失败按无自定义处理（默认渲染），不阻塞主页
    dbgErr('monitor', '自定义布局加载失败, 按默认布局渲染', e);
    layouts.value = {};
    loaded.value = true;
  }
}

// ---------------- 形态键工具 ----------------
export function formFamilyOf(formKey) {
  return String(formKey || '').split(':')[0];
}

export function formLabelOf(formKey) {
  const family = formFamilyOf(formKey);
  const familyLabel = {
    single: '单工位', dual: '双工位', triple: '三工位',
    grid: '多工位总览', zoom: '单通道放大',
  }[family] || formKey;
  const mode = String(formKey || '').split(':')[1];
  const modeLabel = {
    default: '标准', tracking: '清点跟踪', weighing: '称重投料', per_item: '逐件覆盖',
  }[mode];
  return modeLabel ? `${familyLabel} · ${modeLabel}` : familyLabel;
}

// ---------------- 生效布局 ----------------
function effectiveLayoutFor(formKey) {
  if (editMode.value && editFormKey.value === formKey && draft.value) return draft.value;
  const saved = layouts.value[formKey];
  if (!saved || typeof saved !== 'object') return null;
  if (!saved.slots || typeof saved.slots !== 'object') return null;
  if (typeof saved.version !== 'number' || saved.version > LAYOUT_VERSION) {
    // 未来版本的布局数据（降级安装场景）: 不认识 → 回默认，保留数据不动
    return null;
  }
  return saved;
}

export const hasCustomLayout = computed(() => (formKey) => !!layouts.value[formKey]);

export function customizedFormKeys() {
  return Object.keys(layouts.value);
}

// ---------------- DOM 扫描 ----------------
function canvasesFor(formKey) {
  if (!rootEl) return [];
  const family = formFamilyOf(formKey);
  return Array.from(rootEl.querySelectorAll(`[data-layout-canvas="${family}"]`));
}

function slotsIn(canvas) {
  const out = new Map();
  canvas.querySelectorAll('[data-layout-slot]').forEach((el) => {
    const id = el.getAttribute('data-layout-slot');
    if (id && !out.has(id)) out.set(id, el);
  });
  return out;
}

// ---------------- 样式接管 / 还原 ----------------
function absolutize(el, rect, zBase) {
  if (el.__tjLayoutOrig === undefined) {
    el.__tjLayoutOrig = el.getAttribute('style') || '';
  }
  const s = el.style;
  s.position = 'absolute';
  // 注意顺序: inset 是 top/right/bottom/left 简写, 若在 left/top 之后置空会把
  // 刚写的坐标一起抹掉 (真浏览器验证踩过) —— 先归位 right/bottom 再写 left/top
  s.right = 'auto';
  s.bottom = 'auto';
  s.left = `${(rect.x * 100).toFixed(3)}%`;
  s.top = `${(rect.y * 100).toFixed(3)}%`;
  s.width = `${(rect.w * 100).toFixed(3)}%`;
  s.height = `${(rect.h * 100).toFixed(3)}%`;
  s.margin = '0';
  s.minWidth = '0';
  s.minHeight = '0';
  s.maxWidth = 'none';
  s.maxHeight = 'none';
  s.zIndex = String(zBase + (rect.z || 0));
  s.overflow = 'hidden';
  // 显式尺寸下 aspect-ratio 类（三工位视频卡）会与 height 打架, 清掉
  s.aspectRatio = 'auto';
  el.dataset.layoutApplied = '1';
}

function restoreEl(el) {
  if (el.dataset.layoutApplied) {
    el.setAttribute('style', el.__tjLayoutOrig || '');
    if (!el.getAttribute('style')) el.removeAttribute('style');
    delete el.dataset.layoutApplied;
    delete el.__tjLayoutOrig;
  }
}

function restoreCanvas(canvas) {
  slotsIn(canvas).forEach((el) => restoreEl(el));
  if (canvas.dataset.layoutCanvasApplied) {
    canvas.style.position = canvas.__tjLayoutCanvasOrigPos || '';
    delete canvas.dataset.layoutCanvasApplied;
    delete canvas.__tjLayoutCanvasOrigPos;
  }
}

/** 把整个布局系统从当前 DOM 退场（无自定义/出错兜底） */
function restoreAll() {
  if (!rootEl) return;
  rootEl.querySelectorAll('[data-layout-canvas]').forEach((c) => restoreCanvas(c));
}

// ---------------- 自然布局测量（编辑器初稿 / 恢复默认基准） ----------------
/**
 * 测出当前形态第一块画布里全部 slot 的"自然 flow 位置"（画布百分比）。
 * 前提: 调用时画布未被接管（或先还原再测），编辑器进场用它当初稿 ——
 * 初稿即现状, 客户看到的起点与平时完全一致。
 */
export function captureNaturalLayout(formKey) {
  const canvases = canvasesFor(formKey);
  if (!canvases.length) return null;
  const canvas = canvases[0];
  // 先还原（可能正被保存的布局接管），强制回 flow 再测
  canvases.forEach((c) => restoreCanvas(c));
  // 同步读取 rect 前触发 reflow
  void canvas.offsetHeight;
  const cRect = canvas.getBoundingClientRect();
  if (cRect.width < 10 || cRect.height < 10) return null;
  const slots = {};
  slotsIn(canvas).forEach((el, id) => {
    const r = el.getBoundingClientRect();
    slots[id] = {
      x: clamp01((r.left - cRect.left) / cRect.width),
      y: clamp01((r.top - cRect.top) / cRect.height),
      w: Math.max(0.01, Math.min(1, r.width / cRect.width)),
      h: Math.max(0.01, Math.min(1, r.height / cRect.height)),
    };
  });
  if (!Object.keys(slots).length) return null;
  return { version: LAYOUT_VERSION, snap: true, slots };
}

function clamp01(v) {
  return Math.max(0, Math.min(1, Number(v) || 0));
}

// ---------------- 应用器 ----------------
export function requestApply() {
  if (applyScheduled) return;
  applyScheduled = true;
  requestAnimationFrame(() => {
    applyScheduled = false;
    try {
      applyNow();
    } catch (e) {
      // 布局应用任何异常 → 全量退场回默认渲染（主页永不因布局挂）
      dbgErr('monitor', '布局应用异常, 回退默认渲染', e);
      try { restoreAll(); } catch (_) { /* 已尽力 */ }
    }
  });
}

function applyNow() {
  if (!rootEl || !getFormKeyFn) return;
  const formKey = getFormKeyFn();
  const layout = effectiveLayoutFor(formKey);

  // 先把不属于当前形态的残留接管清掉（形态切换时）
  rootEl.querySelectorAll('[data-layout-canvas]').forEach((c) => {
    if (c.getAttribute('data-layout-canvas') !== formFamilyOf(formKey)) restoreCanvas(c);
  });

  const canvases = canvasesFor(formKey);
  if (!layout) {
    canvases.forEach((c) => restoreCanvas(c));
    return;
  }

  let fallbackIdx = 0;
  canvases.forEach((canvas) => {
    if (!canvas.dataset.layoutCanvasApplied) {
      canvas.__tjLayoutCanvasOrigPos = canvas.style.position || '';
      const pos = getComputedStyle(canvas).position;
      if (pos === 'static') canvas.style.position = 'relative';
      canvas.dataset.layoutCanvasApplied = '1';
    }
    slotsIn(canvas).forEach((el, id) => {
      let rect = layout.slots[id];
      if (!rect) {
        // 升级 reconcile: 布局不认识的新区块 → 左下兜底区错开摆放, 可见可再编辑
        rect = {
          ...FALLBACK_RECT,
          x: Math.min(0.66, FALLBACK_RECT.x + (fallbackIdx % 3) * 0.32),
          y: Math.min(0.7, FALLBACK_RECT.y + Math.floor(fallbackIdx / 3) * 0.05),
        };
        fallbackIdx += 1;
      }
      absolutize(el, rect, 5);
    });
  });
}

// ---------------- Monitor 挂载入口 ----------------
/**
 * Monitor 页 onMounted 时调用；返回 detach 清理函数（onUnmounted 调）。
 * getFormKey: () => string 当前形态键（Monitor 内 computed 求值）
 */
export function attachLayoutRoot(el, getFormKey) {
  rootEl = el;
  getFormKeyFn = getFormKey;
  observer = new MutationObserver((muts) => {
    // 只关心节点增减（条件块出现/消失、形态切换重建 DOM）
    for (const m of muts) {
      if (m.type === 'childList' && (m.addedNodes.length || m.removedNodes.length)) {
        requestApply();
        return;
      }
    }
  });
  observer.observe(el, { childList: true, subtree: true });
  // 响应式驱动: 形态键 / 布局数据 / 编辑态 / 草稿任一变化都重排
  const stopWatch = watchEffect(() => {
    getFormKey();
    void layouts.value;
    void loaded.value;
    void editMode.value;
    void draft.value;
    void draftRev.value;
    requestApply();
  });
  return () => {
    try { stopWatch(); } catch (_) { /* noop */ }
    try { observer?.disconnect(); } catch (_) { /* noop */ }
    observer = null;
    try { restoreAll(); } catch (_) { /* noop */ }
    rootEl = null;
    getFormKeyFn = null;
  };
}

export function layoutRuntimeState() {
  return { layouts, loaded, editMode, editFormKey, draft, draftRev, selectedSlot };
}

// ---------------- 编辑会话 ----------------
export function enterEdit(formKey) {
  const saved = layouts.value[formKey];
  // 初稿 = 已保存布局；没保存过 → 现场测量自然布局（起点即现状）
  let base = saved ? JSON.parse(JSON.stringify(saved)) : captureNaturalLayout(formKey);
  if (!base) {
    dbgErr('monitor', '布局编辑初稿生成失败（画布不可见）', formKey);
    return false;
  }
  // 已保存布局也要补测自然位, 给"布局里没有的新区块"当默认（升级场景）
  if (saved) {
    const natural = captureNaturalLayout(formKey);
    if (natural) {
      Object.keys(natural.slots).forEach((id) => {
        if (!base.slots[id]) base.slots[id] = natural.slots[id];
      });
    }
  }
  draft.value = base;
  editFormKey.value = formKey;
  editMode.value = true;
  selectedSlot.value = null;
  requestApply();
  // 编辑态强制显示的条件块 (平时 v-if 隐藏, 如无扫码器时的 MES 条) 此刻才随
  // re-render 进 DOM, 上面的初稿测不到它们 → 双帧后补测一次自然位, 让这些
  // 块落在真实 flow 位置而不是左下兜底区
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (!editMode.value || editFormKey.value !== formKey || !draft.value) return;
    const supplement = captureNaturalLayout(formKey);
    if (supplement) {
      let added = false;
      Object.keys(supplement.slots).forEach((id) => {
        if (!draft.value.slots[id]) {
          draft.value.slots[id] = supplement.slots[id];
          added = true;
        }
      });
      if (added) draftRev.value += 1;
    }
    requestApply(); // 补测会还原画布, 无论有无新增都要重排回接管态
  }));
  return true;
}

/** 编辑器每次落定改动（pointerup / 工具操作）后调用: 通知 + 草稿暂存 */
export function commitDraftChange() {
  draftRev.value += 1;
  requestApply();
  try {
    localStorage.setItem(
      DRAFT_LS_PREFIX + editFormKey.value,
      JSON.stringify({ layout: draft.value, at: Date.now() }),
    );
  } catch (_) { /* 存不进就算了, 草稿只是防崩溃兜底 */ }
}

export function peekDraftBackup(formKey) {
  try {
    const raw = localStorage.getItem(DRAFT_LS_PREFIX + formKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.layout?.slots) return parsed;
  } catch (_) { /* 坏草稿当没有 */ }
  return null;
}

export function clearDraftBackup(formKey) {
  try { localStorage.removeItem(DRAFT_LS_PREFIX + formKey); } catch (_) { /* noop */ }
}

export async function saveDraft() {
  const formKey = editFormKey.value;
  if (!formKey || !draft.value) return false;
  const res = await putMonitorLayout(formKey, draft.value);
  if (res.data?.status === 'ok') {
    layouts.value = { ...layouts.value, [formKey]: JSON.parse(JSON.stringify(draft.value)) };
    clearDraftBackup(formKey);
    dbg('monitor', '自定义布局已保存', `${formKey} (${res.data.slot_count} 区块)`);
    return true;
  }
  return false;
}

export function exitEdit() {
  const formKey = editFormKey.value;
  editMode.value = false;
  editFormKey.value = null;
  draft.value = null;
  selectedSlot.value = null;
  if (formKey) clearDraftBackup(formKey);
  requestApply();
}

export async function restoreFormDefault() {
  const formKey = editFormKey.value;
  if (!formKey) return;
  await deleteMonitorLayout(formKey);
  const next = { ...layouts.value };
  delete next[formKey];
  layouts.value = next;
  clearDraftBackup(formKey);
  // 回自然布局重出初稿, 继续编辑
  draft.value = null;
  requestApply();
  // restore 后 DOM 回 flow, 下一帧再测自然初稿
  await new Promise((r) => requestAnimationFrame(r));
  const natural = captureNaturalLayout(formKey);
  if (natural) {
    draft.value = natural;
    requestApply();
  }
  dbg('monitor', '当前形态布局已恢复默认', formKey);
}

export async function restoreAllDefaults() {
  await deleteAllMonitorLayouts();
  layouts.value = {};
  Object.keys(localStorage)
    .filter((k) => k.startsWith(DRAFT_LS_PREFIX))
    .forEach((k) => { try { localStorage.removeItem(k); } catch (_) { /* noop */ } });
  requestApply();
  dbg('monitor', '全部形态布局已恢复默认');
}
