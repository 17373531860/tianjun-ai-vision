<template>
  <!-- v3.54 检测主页自定义布局编辑器覆盖层
       固定层盖住画布: 屏蔽底层交互, 每个区块画选择框+八向手柄, 拖动=移动/缩放。
       双/三工位编辑第一列, 其余列实时镜像应用。 -->
  <div v-if="editMode" class="tj-layout-editor" data-testid="layout-editor">
    <!-- 工具条 (固定顶部, 永远可达 — 布局改乱也能恢复默认) -->
    <div class="tj-le-toolbar" data-testid="layout-toolbar">
      <span class="tj-le-title">布局编辑 · {{ formLabel }}</span>
      <el-tooltip content="保存当前形态布局并立即对所有工位屏生效" placement="bottom">
        <el-button size="small" type="primary" :loading="saving"
                   data-testid="layout-save" @click="onSave">保存</el-button>
      </el-tooltip>
      <el-button size="small" data-testid="layout-done" @click="onDone">完成</el-button>
      <el-button size="small" data-testid="layout-cancel" @click="onCancel">取消</el-button>
      <el-divider direction="vertical" />
      <el-button size="small" :disabled="!undoStack.length"
                 data-testid="layout-undo" @click="onUndo">撤销</el-button>
      <el-button size="small" :disabled="!redoStack.length"
                 data-testid="layout-redo" @click="onRedo">重做</el-button>
      <el-tooltip content="删除当前形态的自定义布局, 回到出厂排版" placement="bottom">
        <el-button size="small" type="warning" plain
                   data-testid="layout-restore-default" @click="onRestoreDefault">恢复默认</el-button>
      </el-tooltip>
      <el-divider direction="vertical" />
      <span class="tj-le-opt">
        <el-switch v-model="snapOn" size="small" data-testid="layout-snap-switch" />
        <span class="tj-le-opt-label">网格吸附</span>
      </span>
      <span class="tj-le-opt">
        <el-switch v-model="aspectLock" size="small" data-testid="layout-aspect-switch" />
        <span class="tj-le-opt-label">视频锁 16:9</span>
      </span>
      <template v-if="selectedSlot">
        <el-divider direction="vertical" />
        <span class="tj-le-sel">{{ slotLabel(selectedSlot) }}</span>
        <el-button size="small" text data-testid="layout-z-up" @click="bumpZ(1)">上移一层</el-button>
        <el-button size="small" text data-testid="layout-z-down" @click="bumpZ(-1)">下移一层</el-button>
      </template>
      <span class="tj-le-hint">拖动移动 · 边角拉拽缩放 · 方向键微调（Shift=调尺寸）</span>
    </div>

    <!-- 画布覆盖区 -->
    <div v-if="canvasRect" class="tj-le-canvas"
         :style="{
           left: canvasRect.left + 'px', top: canvasRect.top + 'px',
           width: canvasRect.width + 'px', height: canvasRect.height + 'px',
         }"
         @pointerdown.self="selectedSlot = null">
      <!-- 网格参考线 (吸附开启时) -->
      <svg v-if="snapOn" class="tj-le-grid" :width="canvasRect.width" :height="canvasRect.height">
        <line v-for="i in GRID_N - 1" :key="'v' + i"
              :x1="(i / GRID_N) * canvasRect.width" :y1="0"
              :x2="(i / GRID_N) * canvasRect.width" :y2="canvasRect.height" />
        <line v-for="i in GRID_N - 1" :key="'h' + i"
              :x1="0" :y1="(i / GRID_N) * canvasRect.height"
              :x2="canvasRect.width" :y2="(i / GRID_N) * canvasRect.height" />
      </svg>

      <!-- 区块框 -->
      <div v-for="(rect, id) in draftSlots" :key="id"
           class="tj-le-box"
           :class="{ 'is-selected': selectedSlot === id }"
           :style="boxStyle(rect)"
           :data-testid="`layout-box-${id}`"
           @pointerdown.stop.prevent="startMove($event, id)">
        <span class="tj-le-box-label">{{ slotLabel(id) }}</span>
        <template v-if="selectedSlot === id">
          <span v-for="h in HANDLES" :key="h"
                class="tj-le-handle" :class="`h-${h}`"
                :data-testid="`layout-handle-${h}`"
                @pointerdown.stop.prevent="startResize($event, id, h)" />
        </template>
      </div>
    </div>
    <div v-else class="tj-le-empty">当前页面没有可编辑的布局画布（请先切到目标形态）</div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  layoutRuntimeState, formLabelOf, formFamilyOf, commitDraftChange, requestApply,
  saveDraft, exitEdit, restoreFormDefault, peekDraftBackup, clearDraftBackup,
} from './monitorLayout';

const GRID_N = 24;                 // 吸附网格 24×24
const MIN_W = 0.04;
const MIN_H = 0.04;
const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

// 区块中文名 (编辑器展示用; 未登记的 id 原样显示, 不影响功能)
const SLOT_LABELS = {
  video: '视频画面',
  'mes-bar': 'MES 信息条',
  counters: '计数统计',
  quality: '质量摘要（合格率/NG榜/产出）',
  sop: 'SOP 流程卡片',
  'sop-row': 'SOP + 步骤概览',
  'step-table': '步骤统计表',
  controls: '控制按钮',
  stats: '统计面板',
  charts: '图表区',
  'mode-panel': '模式看板（清点/称重/逐件）',
  'weighing-bar': '称重数值条',
  'packaging-card': '包装箱进度',
  'scan-gun': '虚拟扫码枪',
  'periodic-actions': '周期性动作',
  topbar: '顶部信息栏',
  'context-bar': '上下文信息条',
  'summary-row': '摘要图表行',
  toolbar: '总览工具条',
  'grid-area': '工位网格区',
};

const { editMode, editFormKey, draft, selectedSlot } = layoutRuntimeState();

const emit = defineEmits(['exited']);

const saving = ref(false);
const aspectLock = ref(true);
const canvasRect = ref(null);
const undoStack = ref([]);
const redoStack = ref([]);

const formLabel = computed(() => formLabelOf(editFormKey.value));
const draftSlots = computed(() => draft.value?.slots || {});
const snapOn = computed({
  get: () => draft.value?.snap !== false,
  set: (v) => {
    if (draft.value) {
      draft.value.snap = !!v;
      commitDraftChange();
    }
  },
});

function slotLabel(id) {
  return SLOT_LABELS[id] || id;
}

function boxStyle(rect) {
  return {
    left: `${rect.x * 100}%`,
    top: `${rect.y * 100}%`,
    width: `${rect.w * 100}%`,
    height: `${rect.h * 100}%`,
    zIndex: 10 + (rect.z || 0),
  };
}

// ---------------- 画布定位跟踪 ----------------
let rectTimer = null;

function findCanvasEl() {
  const family = formFamilyOf(editFormKey.value);
  return document.querySelector(`[data-layout-canvas="${family}"]`);
}

function refreshCanvasRect() {
  const el = findCanvasEl();
  if (!el) {
    canvasRect.value = null;
    return;
  }
  const r = el.getBoundingClientRect();
  const cur = canvasRect.value;
  if (!cur || Math.abs(cur.left - r.left) > 0.5 || Math.abs(cur.top - r.top) > 0.5
      || Math.abs(cur.width - r.width) > 0.5 || Math.abs(cur.height - r.height) > 0.5) {
    canvasRect.value = { left: r.left, top: r.top, width: r.width, height: r.height };
  }
}

onMounted(() => {
  refreshCanvasRect();
  // 低频轮询兜底 (窗口缩放/侧栏开合都会变), 编辑态才存在本组件, 不影响运行时
  rectTimer = setInterval(refreshCanvasRect, 300);
  window.addEventListener('resize', refreshCanvasRect);
  window.addEventListener('keydown', onKeydown, true);
  maybeOfferDraftRestore();
});

onBeforeUnmount(() => {
  clearInterval(rectTimer);
  window.removeEventListener('resize', refreshCanvasRect);
  window.removeEventListener('keydown', onKeydown, true);
});

watch(editFormKey, (nk) => {
  undoStack.value = [];
  redoStack.value = [];
  refreshCanvasRect();
  // 本组件随 Monitor 常驻挂载 (v-if 只切内层), 草稿恢复提示要挂在
  // "进入编辑" 这一刻而不是 onMounted
  if (nk) maybeOfferDraftRestore();
});

// ---------------- 草稿恢复 (崩溃/误关防丢) ----------------
async function maybeOfferDraftRestore() {
  const formKey = editFormKey.value;
  const backup = peekDraftBackup(formKey);
  if (!backup) return;
  if (JSON.stringify(backup.layout) === JSON.stringify(draft.value)) return;
  try {
    await ElMessageBox.confirm(
      '检测到该形态有一份未保存的编辑草稿（可能来自上次意外退出），是否继续上次的编辑？',
      '恢复未保存的编辑',
      { confirmButtonText: '继续上次编辑', cancelButtonText: '丢弃草稿', type: 'info' },
    );
    pushUndo();
    draft.value = JSON.parse(JSON.stringify(backup.layout));
    commitDraftChange();
  } catch (_) {
    clearDraftBackup(formKey);
  }
}

// ---------------- 撤销 / 重做 ----------------
function pushUndo() {
  undoStack.value.push(JSON.stringify(draft.value));
  if (undoStack.value.length > 50) undoStack.value.shift();
  redoStack.value = [];
}

function onUndo() {
  if (!undoStack.value.length) return;
  redoStack.value.push(JSON.stringify(draft.value));
  draft.value = JSON.parse(undoStack.value.pop());
  commitDraftChange();
}

function onRedo() {
  if (!redoStack.value.length) return;
  undoStack.value.push(JSON.stringify(draft.value));
  draft.value = JSON.parse(redoStack.value.pop());
  commitDraftChange();
}

// ---------------- 拖拽 / 缩放 ----------------
let dragCtx = null;

function snapVal(v) {
  if (!snapOn.value) return v;
  return Math.round(v * GRID_N) / GRID_N;
}

function isVideoSlot(id) {
  return id.includes('video') || id === 'grid-area';
}

function startMove(ev, id) {
  selectedSlot.value = id;
  pushUndo();
  const rect = draft.value.slots[id];
  dragCtx = {
    kind: 'move', id,
    startX: ev.clientX, startY: ev.clientY,
    orig: { ...rect },
  };
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp, { once: true });
}

function startResize(ev, id, handle) {
  selectedSlot.value = id;
  pushUndo();
  const rect = draft.value.slots[id];
  dragCtx = {
    kind: 'resize', id, handle,
    startX: ev.clientX, startY: ev.clientY,
    orig: { ...rect },
  };
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp, { once: true });
}

function onPointerMove(ev) {
  if (!dragCtx || !canvasRect.value) return;
  const dx = (ev.clientX - dragCtx.startX) / canvasRect.value.width;
  const dy = (ev.clientY - dragCtx.startY) / canvasRect.value.height;
  const rect = draft.value.slots[dragCtx.id];
  const o = dragCtx.orig;

  if (dragCtx.kind === 'move') {
    rect.x = snapVal(Math.max(0, Math.min(1 - o.w, o.x + dx)));
    rect.y = snapVal(Math.max(0, Math.min(1 - o.h, o.y + dy)));
  } else {
    let { x, y, w, h } = o;
    const hd = dragCtx.handle;
    if (hd.includes('e')) w = o.w + dx;
    if (hd.includes('s')) h = o.h + dy;
    if (hd.includes('w')) { x = o.x + dx; w = o.w - dx; }
    if (hd.includes('n')) { y = o.y + dy; h = o.h - dy; }
    // 吸附作用在"目标边"上
    if (snapOn.value) {
      if (hd.includes('e')) w = snapVal(x + w) - x;
      if (hd.includes('s')) h = snapVal(y + h) - y;
      if (hd.includes('w')) { const nx = snapVal(x); w += x - nx; x = nx; }
      if (hd.includes('n')) { const ny = snapVal(y); h += y - ny; y = ny; }
    }
    w = Math.max(MIN_W, w);
    h = Math.max(MIN_H, h);
    // 视频区块可选锁 16:9 (像素比, 折算画布分数)
    if (aspectLock.value && isVideoSlot(dragCtx.id) && canvasRect.value) {
      const pxW = w * canvasRect.value.width;
      h = (pxW * 9 / 16) / canvasRect.value.height;
      h = Math.max(MIN_H, h);
    }
    x = Math.max(0, Math.min(x, 1 - MIN_W));
    y = Math.max(0, Math.min(y, 1 - MIN_H));
    w = Math.min(w, 1 - x);
    h = Math.min(h, 1 - y);
    Object.assign(rect, { x, y, w, h });
  }
  requestApply();
}

function onPointerUp() {
  window.removeEventListener('pointermove', onPointerMove);
  if (dragCtx) {
    // 落定: 四舍五入到 0.01% 精度, 提交草稿
    const rect = draft.value.slots[dragCtx.id];
    ['x', 'y', 'w', 'h'].forEach((f) => { rect[f] = Math.round(rect[f] * 10000) / 10000; });
    dragCtx = null;
    commitDraftChange();
  }
}

// ---------------- 键盘微调 ----------------
function onKeydown(ev) {
  if (!selectedSlot.value || !draft.value) return;
  const keys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Escape'];
  if (!keys.includes(ev.key)) return;
  ev.preventDefault();
  ev.stopPropagation();
  if (ev.key === 'Escape') {
    selectedSlot.value = null;
    return;
  }
  const step = snapOn.value ? 1 / GRID_N : 0.005;
  const rect = draft.value.slots[selectedSlot.value];
  pushUndo();
  const dx = ev.key === 'ArrowLeft' ? -step : ev.key === 'ArrowRight' ? step : 0;
  const dy = ev.key === 'ArrowUp' ? -step : ev.key === 'ArrowDown' ? step : 0;
  if (ev.shiftKey) {
    rect.w = Math.max(MIN_W, Math.min(1 - rect.x, rect.w + dx));
    rect.h = Math.max(MIN_H, Math.min(1 - rect.y, rect.h + dy));
  } else {
    rect.x = Math.max(0, Math.min(1 - rect.w, rect.x + dx));
    rect.y = Math.max(0, Math.min(1 - rect.h, rect.y + dy));
  }
  commitDraftChange();
}

// ---------------- z 序 ----------------
function bumpZ(delta) {
  const rect = draft.value?.slots?.[selectedSlot.value];
  if (!rect) return;
  pushUndo();
  rect.z = Math.max(0, Math.min(50, (rect.z || 0) + delta));
  commitDraftChange();
}

// ---------------- 保存 / 退出 ----------------
async function onSave() {
  saving.value = true;
  try {
    const ok = await saveDraft();
    if (ok) {
      // 保存即新基线: 清撤销栈, 之后直接点"完成"不再弹未保存确认
      undoStack.value = [];
      redoStack.value = [];
      ElMessage.success('布局已保存，对所有工位屏立即生效');
    } else {
      ElMessage.error('布局保存失败');
    }
  } catch (e) {
    ElMessage.error(`布局保存失败: ${e?.response?.data?.detail || e.message}`);
  } finally {
    saving.value = false;
  }
}

function isDirty() {
  // 以"用户是否动过"为准 (撤销/重做栈非空): 初次进编辑 draft 是现场测量的
  // 初稿、且补测会异步加 slot, 拿它和已保存布局做 JSON 对比会误报脏
  return undoStack.value.length > 0 || redoStack.value.length > 0;
}

async function onDone() {
  if (draft.value && isDirty()) {
    try {
      await ElMessageBox.confirm('有未保存的布局改动，保存后退出？', '退出布局编辑', {
        confirmButtonText: '保存并退出',
        cancelButtonText: '放弃改动退出',
        distinguishCancelAndClose: true,
        type: 'warning',
      });
      const ok = await saveDraft();
      if (!ok) return;
      ElMessage.success('布局已保存');
    } catch (action) {
      if (action === 'close') return; // 点 X = 继续编辑
      // cancel = 放弃改动退出
    }
  }
  exitEdit();
  emit('exited');
}

function onCancel() {
  exitEdit();
  emit('exited');
}

async function onRestoreDefault() {
  try {
    await ElMessageBox.confirm(
      `将删除「${formLabel.value}」形态的自定义布局，恢复出厂排版（其他形态不受影响）。`,
      '恢复默认布局',
      { confirmButtonText: '恢复默认', cancelButtonText: '取消', type: 'warning' },
    );
  } catch (_) {
    return;
  }
  try {
    await restoreFormDefault();
    undoStack.value = [];
    redoStack.value = [];
    ElMessage.success('已恢复默认布局');
  } catch (e) {
    ElMessage.error(`恢复默认失败: ${e?.response?.data?.detail || e.message}`);
  }
}
</script>

<style scoped>
.tj-layout-editor {
  position: fixed;
  inset: 0;
  z-index: 3000;
  pointer-events: none;
}

.tj-le-toolbar {
  position: fixed;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 3200;
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  max-width: 96vw;
  padding: 8px 14px;
  background: rgba(15, 23, 42, 0.96);
  border: 1px solid #0891b2;
  border-radius: 10px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.5);
}

.tj-le-title {
  color: #22d3ee;
  font-size: 13px;
  font-weight: 600;
  margin-right: 4px;
}

.tj-le-opt {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.tj-le-opt-label {
  color: #cbd5e1;
  font-size: 12px;
}

.tj-le-sel {
  color: #fbbf24;
  font-size: 12px;
}

.tj-le-hint {
  color: #64748b;
  font-size: 11px;
}

.tj-le-canvas {
  position: fixed;
  pointer-events: auto;
  background: rgba(8, 145, 178, 0.04);
  outline: 2px dashed rgba(34, 211, 238, 0.5);
  outline-offset: -2px;
}

.tj-le-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.tj-le-grid line {
  stroke: rgba(34, 211, 238, 0.1);
  stroke-width: 1;
}

.tj-le-box {
  position: absolute;
  box-sizing: border-box;
  border: 1.5px solid rgba(34, 211, 238, 0.65);
  background: rgba(8, 145, 178, 0.08);
  cursor: move;
  user-select: none;
}

.tj-le-box.is-selected {
  border-color: #fbbf24;
  background: rgba(251, 191, 36, 0.1);
}

.tj-le-box-label {
  position: absolute;
  top: 2px;
  left: 4px;
  font-size: 11px;
  color: #e2e8f0;
  background: rgba(15, 23, 42, 0.85);
  padding: 1px 6px;
  border-radius: 4px;
  pointer-events: none;
  white-space: nowrap;
}

.tj-le-handle {
  position: absolute;
  width: 10px;
  height: 10px;
  background: #fbbf24;
  border: 1.5px solid #0f172a;
  border-radius: 2px;
  z-index: 5;
}

.h-nw { top: -5px; left: -5px; cursor: nwse-resize; }
.h-n  { top: -5px; left: calc(50% - 5px); cursor: ns-resize; }
.h-ne { top: -5px; right: -5px; cursor: nesw-resize; }
.h-e  { top: calc(50% - 5px); right: -5px; cursor: ew-resize; }
.h-se { bottom: -5px; right: -5px; cursor: nwse-resize; }
.h-s  { bottom: -5px; left: calc(50% - 5px); cursor: ns-resize; }
.h-sw { bottom: -5px; left: -5px; cursor: nesw-resize; }
.h-w  { top: calc(50% - 5px); left: -5px; cursor: ew-resize; }

.tj-le-empty {
  position: fixed;
  top: 60px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 3100;
  pointer-events: auto;
  color: #fbbf24;
  background: rgba(15, 23, 42, 0.9);
  border: 1px solid #475569;
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 13px;
}
</style>
