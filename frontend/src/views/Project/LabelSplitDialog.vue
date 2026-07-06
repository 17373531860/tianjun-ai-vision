<template>
  <!-- 同标签区域拆分规则编辑器 (v3.32): 快照底图上画多个命名区域, 把一个原始标签拆成多个虚拟步骤 -->
  <el-dialog :model-value="visible"
    :title="rule && rule.source_label ? `拆分规则 · ${rule.source_label}` : '新建同标签区域拆分规则'"
    width="92%" :close-on-click-modal="false" destroy-on-close
    @update:model-value="$emit('update:visible', $event)"
    @close="$emit('close')">
    <div v-if="rule" class="flex gap-4" style="min-height: 60vh;">
      <!-- 左: 画布 -->
      <div class="flex-1 min-w-0 flex flex-col gap-2">
        <div class="flex items-center gap-2 text-xs flex-wrap">
          <template v-if="editingRegionIdx >= 0">
            <span class="text-amber-400 font-bold">正在绘制: {{ activeRegions[editingRegionIdx]?.name || `区域${editingRegionIdx + 1}` }}</span>
            <span class="text-gray-400">单击加点, 点第一个点或双击闭合</span>
            <el-button size="small" @click="undoPoint" :disabled="drawPoints.length === 0">撤销上一点</el-button>
            <el-button size="small" type="success" @click="finishPolygon" :disabled="drawPoints.length < 3">完成绘制</el-button>
            <el-button size="small" @click="cancelDrawing">取消</el-button>
          </template>
          <template v-else>
            <span class="text-gray-400">在右侧区域列表点「画区域」开始绘制; 已画好的区域直接显示在画面上</span>
          </template>
          <div class="flex-1"></div>
          <el-button size="small" @click="reloadSnapshot">刷新画面快照</el-button>
        </div>
        <div class="relative bg-black rounded overflow-hidden flex justify-center" style="max-height: 62vh;">
          <canvas ref="canvasRef" class="cursor-crosshair" style="max-width: 100%; max-height: 62vh; object-fit: contain;"
            @click="onCanvasClick" @dblclick="onCanvasDblClick" @mousemove="onCanvasMouseMove"></canvas>
        </div>
        <div class="text-[11px] text-gray-500">
          区域按检测框<b class="text-gray-300">中心点</b>命中判定; 建议区域画大一点、彼此留空隙, 减少边界抖动。
          <template v-if="rule.mode === 'anchor'">锚点跟随模式下, 区域按「当前锚点框 vs 标定锚点框」平移缩放, 白色虚线框 = 标定锚点位置。</template>
        </div>
      </div>

      <!-- 右: 规则表单 -->
      <div class="w-96 flex-shrink-0 overflow-y-auto custom-scrollbar space-y-4 pr-1" style="max-height: 66vh;">
        <div>
          <div class="text-xs text-gray-400 mb-1">模型原始标签（要被拆分的动作标签）</div>
          <el-select v-model="rule.source_label" size="small" filterable allow-create default-first-option
            :placeholder="(modelLabels || []).length ? '从模型类别里选' : '先在基础设置选模型'" class="!w-full">
            <el-option v-for="lbl in (modelLabels || [])" :key="lbl" :label="lbl" :value="lbl" />
          </el-select>
        </div>

        <div>
          <div class="text-xs text-gray-400 mb-1">区域定位方式</div>
          <el-radio-group v-model="rule.mode" size="small">
            <el-radio-button value="fixed">固定画面</el-radio-button>
            <el-radio-button value="anchor">锚点跟随</el-radio-button>
          </el-radio-group>
          <div class="text-[10px] text-gray-500 mt-1">
            固定画面: 区域钉死在画面上, 配合「工件就位提示」让工人把工件放进引导框。<br/>
            锚点跟随: 区域跟着某个检测目标（如已装好的罩子）平移缩放, 工件位置有偏差也能对上。
          </div>
        </div>

        <div v-if="rule.mode === 'anchor'" class="border border-slate-700 rounded p-2 space-y-2">
          <div>
            <div class="text-xs text-gray-400 mb-1">锚点标签（跟随哪个检测目标）</div>
            <el-select v-model="rule.anchor_label" size="small" filterable allow-create default-first-option
              placeholder="如: 前罩" class="!w-full">
              <el-option v-for="lbl in (modelLabels || [])" :key="lbl" :label="lbl" :value="lbl" />
            </el-select>
          </div>
          <div class="flex items-center gap-2">
            <el-button size="small" type="primary" plain :loading="grabbingAnchor" @click="grabAnchorRef">
              从当前画面抓取锚点框
            </el-button>
            <span v-if="rule.anchor_ref" class="text-[10px] text-green-400">已标定</span>
            <span v-else class="text-[10px] text-amber-400">未标定</span>
          </div>
          <div v-if="rule.anchor_ref" class="text-[10px] text-gray-500 font-mono">
            x={{ rule.anchor_ref.x.toFixed(3) }} y={{ rule.anchor_ref.y.toFixed(3) }}
            w={{ rule.anchor_ref.w.toFixed(3) }} h={{ rule.anchor_ref.h.toFixed(3) }}
          </div>
          <div class="flex items-center gap-2 text-xs text-gray-400">
            <span>锚点丢失沿用最近位置</span>
            <el-input-number v-model="rule.anchor_hold_seconds" :min="0" :max="60" :step="0.5" :precision="1" size="small" class="!w-24" />
            <span>秒</span>
          </div>
          <div class="text-[10px] text-gray-500">
            标定流程: 先在监控页启动检测并把工件放到标准位置 → 回到这里点「抓取锚点框」→ 再对着快照画区域。
            运行时区域会按锚点当前位置自动平移缩放（不支持旋转, 现场请用定位销/托盘约束朝向）。
          </div>
        </div>

        <div>
          <div class="text-xs text-gray-400 mb-1">未命中任何区域的「{{ rule.source_label || '原始标签' }}」如何处理</div>
          <el-select v-model="rule.unmatched" size="small" class="!w-full">
            <el-option label="丢弃（不参与任何判定, 默认）" value="drop" />
            <el-option label="保留原标签（继续走原始标签的步骤逻辑）" value="keep" />
            <el-option label="改写成指定标签（可配成“位置外操作”步骤挂报警）" value="map" />
          </el-select>
          <el-input v-if="rule.unmatched === 'map'" v-model="rule.unmatched_label" size="small"
            placeholder="如: 位置外打螺丝" class="!w-full mt-1" />
          <div v-if="rule.unmatched === 'map'" class="text-[10px] text-gray-500 mt-1">
            保存后会自动生成同名步骤（默认禁用）; 要参与判定/挂事件请到步骤表启用并配置。
          </div>
        </div>

        <div class="border border-slate-700 rounded p-2 space-y-2">
          <div class="flex items-center gap-2">
            <span class="text-xs font-bold text-white">多轮次拆分</span>
            <el-switch v-model="rule.rounds.enabled" size="small" />
            <div class="flex-1"></div>
          </div>
          <div class="text-[10px] text-gray-500 leading-relaxed">
            同一批位置在不同工序轮次代表不同步骤时启用（如前罩/后罩各打4颗、打的位置重叠）。
            每次检测到「轮次切换标签」<b class="text-gray-300">重新出现</b>就进入下一轮, 满轮后回到第1轮;
            虚拟步骤名 = 当轮前缀 + 区域名（如 前罩螺丝1 / 后罩螺丝1）。
          </div>
          <template v-if="rule.rounds.enabled">
            <div class="flex items-center gap-2">
              <span class="text-xs text-gray-400 flex-shrink-0">轮次切换标签</span>
              <el-select v-model="rule.rounds.trigger_label" size="small" filterable allow-create default-first-option
                placeholder="如: 盖罩" class="!w-40">
                <el-option v-for="lbl in (modelLabels || [])" :key="lbl" :label="lbl" :value="lbl" />
              </el-select>
              <span class="text-xs text-gray-400">轮数</span>
              <el-input-number :model-value="rule.rounds.count" :min="2" :max="8" size="small" class="!w-24"
                @update:model-value="setRoundCount" />
            </div>
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-xs text-gray-400 flex-shrink-0">每轮前缀</span>
              <el-input v-for="(_, i) in rule.rounds.prefixes" :key="i" v-model="rule.rounds.prefixes[i]"
                size="small" class="!w-24" :placeholder="`第${i + 1}轮`" />
            </div>
            <div class="flex items-center gap-2 text-xs text-gray-400">
              <span>切换标签离场超过</span>
              <el-input-number v-model="rule.rounds.trigger_gap_seconds" :min="0.5" :max="60" :step="0.5" :precision="1"
                size="small" class="!w-24" />
              <span>秒后再出现才算新一轮（防遮挡误切）</span>
            </div>
            <div class="text-[10px] text-gray-500">
              周期结算且切换标签离场后轮次自动归零, 下一个工件从第1轮重新开始。
              默认所有轮共用同一批区域; 若翻面后位置对不上, 在下方区域列表切到对应轮单独画一批。
            </div>
          </template>
        </div>

        <div class="border border-slate-700 rounded">
          <div class="px-2 py-1.5 bg-slate-800 border-b border-slate-700 flex items-center gap-2">
            <span class="text-xs font-bold text-white">区域列表（区域名 = 虚拟步骤名）</span>
            <div class="flex-1"></div>
            <el-button size="small" @click="applyQuadrantTemplate">四象限模板</el-button>
            <el-button size="small" type="primary" plain @click="addRegion">添加区域</el-button>
          </div>
          <!-- 多轮次开启时: 选择在编辑哪一轮的区域（共享 = 所有未单独配的轮通用） -->
          <div v-if="rule.rounds && rule.rounds.enabled" class="px-2 pt-2 space-y-1">
            <el-radio-group v-model="regionScope" size="small" @change="onScopeChange">
              <el-radio-button :value="0">共享区域</el-radio-button>
              <el-radio-button v-for="i in rule.rounds.count" :key="i" :value="i">
                第{{ i }}轮·{{ rule.rounds.prefixes[i - 1] || '' }}
              </el-radio-button>
            </el-radio-group>
            <div v-if="regionScope > 0" class="text-[10px]"
              :class="activeIsOverride ? 'text-cyan-400' : 'text-gray-500'">
              {{ activeIsOverride
                ? `第${regionScope}轮使用独立区域（不再沿用共享区域）`
                : `第${regionScope}轮当前沿用共享区域; 添加/画区域后本轮改用独立的一批` }}
              <el-button v-if="activeIsOverride" size="small" type="danger" text
                @click="clearRoundOverride">恢复沿用共享区域</el-button>
            </div>
          </div>
          <div class="p-2 space-y-2">
            <div v-for="(region, idx) in activeRegions" :key="idx"
              class="flex items-center gap-2 border border-slate-700 rounded px-2 py-1.5"
              :class="editingRegionIdx === idx ? 'border-amber-400 bg-amber-400/5' : ''">
              <el-color-picker v-model="region.color" size="small"
                :predefine="REGION_PALETTE" @change="redraw" />
              <el-input v-model="region.name" size="small" placeholder="区域名(如 螺丝1)" class="!w-32" @input="redraw" />
              <span class="text-[10px]" :class="region.polygon && region.polygon.length >= 3 ? 'text-green-400' : 'text-amber-400'">
                {{ region.polygon && region.polygon.length >= 3 ? `${region.polygon.length}点` : '未画' }}
              </span>
              <div class="flex-1"></div>
              <el-button size="small" type="primary" plain @click="startDrawing(idx)">
                {{ region.polygon && region.polygon.length >= 3 ? '重画' : '画区域' }}
              </el-button>
              <el-button size="small" type="danger" plain @click="removeRegion(idx)">删</el-button>
            </div>
            <div v-if="activeRegions.length === 0" class="text-center text-gray-500 text-xs py-3">
              {{ regionScope > 0
                ? '本轮还没有独立区域 — 点「添加区域」开始画; 也可用「复制共享区域」再逐个挪位置'
                : '还没有区域 — 点「添加区域」逐个画, 或用「四象限模板」一键生成再微调' }}
            </div>
            <div v-if="regionScope > 0 && !activeIsOverride && (rule.regions || []).length" class="text-center">
              <el-button size="small" plain @click="copySharedToRound">复制共享区域到本轮再调整</el-button>
            </div>
          </div>
        </div>

        <div class="text-[10px] text-gray-500 leading-relaxed">
          保存规则后, 每个区域名会自动在「步骤表」生成/同步同名虚拟步骤（带“拆分”徽标）,
          其阈值 / 持续时间 / 顺序等与普通步骤完全一致, 到步骤表和逻辑设置里配即可。
        </div>
      </div>
    </div>
    <template #footer>
      <el-button @click="$emit('update:visible', false)">取消</el-button>
      <el-button type="primary" @click="handleSave">保存规则</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
// ==================== 同标签区域拆分编辑器 ====================
// 数据流: 父级 openLabelSplitEditor 时传入规则的深拷贝 (load 方法),
// 本组件原位编辑该拷贝, 点「保存规则」emit('save', rule) 交回父级写入
// pipeline_config.label_splits 并同步虚拟步骤; 落库仍走父级「保存配置」。
// 画布交互与 RoiEditorDialog 同一套(单击加点/近首点闭合/双击闭合), 扩展成多区域。
import { ref, reactive, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { getBackendHost } from '@/api/index';
import { getDetectionResults } from '@/api/detection';
import { QUADRANT_TEMPLATE } from './labelSplit';

defineProps({
  visible: { type: Boolean, required: true },
  modelLabels: { type: Array, default: () => [] },
});
const emit = defineEmits(['update:visible', 'save', 'close']);

const REGION_PALETTE = ['#f97316', '#22d3ee', '#a78bfa', '#84cc16', '#ec4899', '#facc15', '#38bdf8', '#f87171'];

const canvasRef = ref(null);
const rule = ref(null);
const editingRegionIdx = ref(-1);
// 区域编辑作用域: 0=共享区域, n=第n轮独立区域(rounds.region_overrides[n])
const regionScope = ref(0);
const drawPoints = reactive([]);   // 绘制中的像素点
const grabbingAnchor = ref(false);
let snapshotImage = null;
let mousePos = null;
let snapshotChannel = 0;
const CLOSE_RADIUS = 15;

// ---------- 打开/快照 ----------

const loadSnapshot = () => new Promise((resolve) => {
  const canvas = canvasRef.value;
  if (!canvas) { resolve(false); return; }
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.src = `${getBackendHost()}/snapshot?channel=${snapshotChannel}&t=${Date.now()}`;
  img.onload = () => {
    snapshotImage = img;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    redraw();
    resolve(true);
  };
  img.onerror = () => {
    snapshotImage = null;
    canvas.width = 960;
    canvas.height = 540;
    redraw();
    resolve(false);
  };
});

// 父级打开对话框后调用: 传入规则深拷贝 + 快照通道
const load = async (channel, ruleCopy) => {
  snapshotChannel = channel || 0;
  // v3.32 之前保存的老规则没有 rounds 段, 补默认值避免模板绑定 undefined
  if (!ruleCopy.rounds || typeof ruleCopy.rounds !== 'object') {
    ruleCopy.rounds = { enabled: false, trigger_label: '', count: 2,
                        prefixes: ['前罩', '后罩'], trigger_gap_seconds: 3.0 };
  }
  if (!ruleCopy.rounds.region_overrides || typeof ruleCopy.rounds.region_overrides !== 'object') {
    ruleCopy.rounds.region_overrides = {};
  }
  rule.value = ruleCopy;
  regionScope.value = 0;
  editingRegionIdx.value = -1;
  drawPoints.length = 0;
  mousePos = null;
  await loadSnapshot();
};
defineExpose({ load });

const reloadSnapshot = () => loadSnapshot();

// 轮数增减时同步 prefixes 数组长度（新增轮默认前缀"第N轮"）; 缩轮时丢弃超界的独立区域
const setRoundCount = (val) => {
  const cnt = Math.max(2, Math.min(8, Number(val) || 2));
  const r = rule.value.rounds;
  r.count = cnt;
  const prefixes = Array.isArray(r.prefixes) ? r.prefixes.slice(0, cnt) : [];
  while (prefixes.length < cnt) prefixes.push(`第${prefixes.length + 1}轮`);
  r.prefixes = prefixes;
  for (const key of Object.keys(r.region_overrides || {})) {
    if (Number(key) > cnt) delete r.region_overrides[key];
  }
  if (regionScope.value > cnt) { regionScope.value = 0; cancelDrawing(); }
};

// ---------- 区域作用域（共享 / 每轮独立） ----------

// 当前作用域下正在展示/编辑的区域数组（轮作用域无独立区域时返回空数组, 编辑时再建）
const activeRegions = computed(() => {
  const r = rule.value;
  if (!r) return [];
  if (regionScope.value === 0 || !r.rounds || !r.rounds.enabled) return r.regions || [];
  return r.rounds.region_overrides?.[String(regionScope.value)] || [];
});

const activeIsOverride = computed(() =>
  regionScope.value > 0 && (activeRegions.value || []).length > 0);

// 写入口: 需要修改时保证目标数组存在
const ensureActiveList = () => {
  const r = rule.value;
  if (regionScope.value === 0 || !r.rounds || !r.rounds.enabled) {
    if (!Array.isArray(r.regions)) r.regions = [];
    return r.regions;
  }
  if (!r.rounds.region_overrides || typeof r.rounds.region_overrides !== 'object') {
    r.rounds.region_overrides = {};
  }
  const key = String(regionScope.value);
  if (!Array.isArray(r.rounds.region_overrides[key])) r.rounds.region_overrides[key] = [];
  return r.rounds.region_overrides[key];
};

const onScopeChange = () => {
  cancelDrawing();
};

const clearRoundOverride = () => {
  cancelDrawing();
  const r = rule.value;
  if (r?.rounds?.region_overrides) delete r.rounds.region_overrides[String(regionScope.value)];
  redraw();
};

const copySharedToRound = () => {
  const list = ensureActiveList();
  list.length = 0;
  for (const g of (rule.value.regions || [])) {
    list.push({ name: g.name, polygon: Array.isArray(g.polygon) ? g.polygon.map(p => [...p]) : null, color: g.color });
  }
  redraw();
  ElMessage.success(`共享区域已复制到第${regionScope.value}轮, 逐个「重画」挪到翻面后的位置`);
};

// ---------- 区域列表 ----------

const addRegion = () => {
  const list = ensureActiveList();
  const n = list.length;
  list.push({
    name: `区域${n + 1}`,
    polygon: null,
    color: REGION_PALETTE[n % REGION_PALETTE.length],
  });
  startDrawing(list.length - 1);
};

const removeRegion = (idx) => {
  if (editingRegionIdx.value === idx) cancelDrawing();
  ensureActiveList().splice(idx, 1);
  redraw();
};

const applyQuadrantTemplate = () => {
  cancelDrawing();
  const list = ensureActiveList();
  list.length = 0;
  for (const r of QUADRANT_TEMPLATE) {
    list.push({ name: r.name, polygon: r.polygon.map(p => [...p]), color: r.color });
  }
  redraw();
  ElMessage.success('已生成四象限区域, 可逐个重画/改名');
};

// ---------- 绘制交互 ----------

const startDrawing = (idx) => {
  editingRegionIdx.value = idx;
  drawPoints.length = 0;
  mousePos = null;
  redraw();
};

const cancelDrawing = () => {
  editingRegionIdx.value = -1;
  drawPoints.length = 0;
  mousePos = null;
  redraw();
};

const canvasXY = (e) => {
  const canvas = canvasRef.value;
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  return {
    x: (e.clientX - rect.left) * (canvas.width / rect.width),
    y: (e.clientY - rect.top) * (canvas.height / rect.height),
  };
};

const onCanvasClick = (e) => {
  if (editingRegionIdx.value < 0) return;
  const pt = canvasXY(e);
  if (!pt) return;
  if (drawPoints.length >= 3) {
    const first = drawPoints[0];
    const canvas = canvasRef.value;
    const scale = canvas.width / (canvas.getBoundingClientRect().width || 1);
    if (Math.hypot(pt.x - first.x, pt.y - first.y) < CLOSE_RADIUS * scale) {
      finishPolygon();
      return;
    }
  }
  drawPoints.push(pt);
  redraw();
};

const onCanvasDblClick = (e) => {
  e.preventDefault();
  if (editingRegionIdx.value >= 0 && drawPoints.length >= 3) finishPolygon();
};

const onCanvasMouseMove = (e) => {
  if (editingRegionIdx.value < 0) return;
  mousePos = canvasXY(e);
  redraw();
};

const undoPoint = () => {
  drawPoints.pop();
  redraw();
};

const finishPolygon = () => {
  if (editingRegionIdx.value < 0 || drawPoints.length < 3) return;
  const canvas = canvasRef.value;
  const w = canvas?.width || 1;
  const h = canvas?.height || 1;
  const polygon = drawPoints.map(pt => [
    Math.round((pt.x / w) * 10000) / 10000,
    Math.round((pt.y / h) * 10000) / 10000,
  ]);
  const target = activeRegions.value[editingRegionIdx.value];
  if (target) target.polygon = polygon;
  cancelDrawing();
};

// ---------- 锚点标定 ----------

const grabAnchorRef = async () => {
  const lbl = String(rule.value.anchor_label || '').trim();
  if (!lbl) { ElMessage.warning('先选择锚点标签'); return; }
  grabbingAnchor.value = true;
  try {
    const res = await getDetectionResults(snapshotChannel);
    const dets = (res.data?.detections || []).filter(d => d.label === lbl);
    if (!dets.length) {
      ElMessage.error(`当前画面没有检测到「${lbl}」— 请先在监控页启动检测并把工件放到标准位置`);
      return;
    }
    const best = dets.reduce((a, b) => ((b.confidence || 0) > (a.confidence || 0) ? b : a));
    rule.value.anchor_ref = {
      x: Number(best.x) || 0, y: Number(best.y) || 0,
      w: Number(best.w) || 0, h: Number(best.h) || 0,
    };
    redraw();
    ElMessage.success(`已标定锚点框（置信度 ${(best.confidence * 100).toFixed(0)}%）, 请保持工件位置不动再画区域`);
  } catch (e) {
    ElMessage.error('抓取失败: ' + (e.message || e));
  } finally {
    grabbingAnchor.value = false;
  }
};

// ---------- 渲染 ----------

const redraw = () => {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (snapshotImage) {
    ctx.drawImage(snapshotImage, 0, 0);
  } else {
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#94a3b8';
    ctx.font = '16px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('无法获取摄像头画面（可先用模板/盲画, 稍后再校准）', W / 2, H / 2);
    ctx.textAlign = 'left';
  }
  const r = rule.value;
  if (!r) return;

  // 已有区域（当前作用域: 共享 或 某轮独立）
  (activeRegions.value || []).forEach((region, idx) => {
    if (idx === editingRegionIdx.value) return;  // 正在重画的旧形状不画
    const poly = region.polygon;
    if (!Array.isArray(poly) || poly.length < 3) return;
    const color = region.color || '#22d3ee';
    ctx.beginPath();
    poly.forEach(([nx, ny], i) => {
      const x = nx * W, y = ny * H;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.closePath();
    ctx.save();
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.18;
    ctx.fill();
    ctx.restore();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();
    // 区域名放形心
    const cx = poly.reduce((s, p) => s + p[0], 0) / poly.length * W;
    const cy = poly.reduce((s, p) => s + p[1], 0) / poly.length * H;
    ctx.font = 'bold 16px Arial';
    const tw = ctx.measureText(region.name || '').width;
    ctx.fillStyle = 'rgba(0,0,0,0.55)';
    ctx.fillRect(cx - tw / 2 - 4, cy - 12, tw + 8, 22);
    ctx.fillStyle = color;
    ctx.fillText(region.name || '', cx - tw / 2, cy + 5);
  });

  // anchor 模式: 标定锚点框（白色虚线）
  if (r.mode === 'anchor' && r.anchor_ref && r.anchor_ref.w > 0) {
    const a = r.anchor_ref;
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.9)';
    ctx.setLineDash([8, 5]);
    ctx.lineWidth = 2;
    ctx.strokeRect(a.x * W, a.y * H, a.w * W, a.h * H);
    ctx.setLineDash([]);
    ctx.font = 'bold 13px Arial';
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fillText(`锚点标定: ${r.anchor_label || ''}`, a.x * W + 4, a.y * H - 6);
    ctx.restore();
  }

  // 绘制中的多边形
  if (editingRegionIdx.value >= 0 && drawPoints.length > 0) {
    const color = activeRegions.value[editingRegionIdx.value]?.color || '#f59e0b';
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(drawPoints[0].x, drawPoints[0].y);
    for (let i = 1; i < drawPoints.length; i++) ctx.lineTo(drawPoints[i].x, drawPoints[i].y);
    if (mousePos) ctx.lineTo(mousePos.x, mousePos.y);
    ctx.stroke();
    ctx.setLineDash([]);
    const scale = W / (canvas.getBoundingClientRect().width || 1);
    const nearFirst = drawPoints.length >= 3 && mousePos &&
      Math.hypot(mousePos.x - drawPoints[0].x, mousePos.y - drawPoints[0].y) < CLOSE_RADIUS * scale;
    drawPoints.forEach((pt, i) => {
      const isFirst = i === 0;
      ctx.fillStyle = isFirst ? (nearFirst ? '#22c55e' : '#f59e0b') : color;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, isFirst && nearFirst ? 9 : 5, 0, Math.PI * 2);
      ctx.fill();
    });
  }
};

// ---------- 保存 ----------

const handleSave = () => {
  const r = rule.value;
  if (!r) return;
  if (!String(r.source_label || '').trim()) { ElMessage.warning('请选择模型原始标签'); return; }
  const validRegions = (r.regions || []).filter(
    g => g && String(g.name || '').trim() && Array.isArray(g.polygon) && g.polygon.length >= 3
  );
  if (validRegions.length === 0) { ElMessage.warning('至少画好一个区域并命名'); return; }
  const names = new Set();
  for (const g of validRegions) {
    const n = String(g.name).trim();
    if (names.has(n)) { ElMessage.warning(`区域名「${n}」重复, 请改名`); return; }
    names.add(n);
  }
  if (r.mode === 'anchor') {
    if (!String(r.anchor_label || '').trim()) { ElMessage.warning('锚点跟随模式需要选择锚点标签'); return; }
    if (!r.anchor_ref || !(r.anchor_ref.w > 0)) { ElMessage.warning('锚点跟随模式需要先「抓取锚点框」完成标定'); return; }
  }
  if (r.unmatched === 'map' && !String(r.unmatched_label || '').trim()) {
    ElMessage.warning('「改写成指定标签」需要填写目标标签');
    return;
  }
  if (r.rounds && r.rounds.enabled) {
    if (!String(r.rounds.trigger_label || '').trim()) { ElMessage.warning('多轮次需要选择「轮次切换标签」'); return; }
    const prefixes = (r.rounds.prefixes || []).map(p => String(p || '').trim());
    if (prefixes.some(p => !p)) { ElMessage.warning('多轮次每一轮都要填前缀'); return; }
    if (new Set(prefixes).size !== prefixes.length) { ElMessage.warning('轮次前缀不能重复'); return; }
    r.rounds.prefixes = prefixes;
    // 每轮独立区域: 只保留画完整的（全没画完 = 该轮回退共享区域）; 同轮内名字查重
    const overrides = {};
    for (const [key, list] of Object.entries(r.rounds.region_overrides || {})) {
      const valid = (list || []).filter(
        g => g && String(g.name || '').trim() && Array.isArray(g.polygon) && g.polygon.length >= 3
      );
      const ns = new Set();
      for (const g of valid) {
        const n = String(g.name).trim();
        if (ns.has(n)) { ElMessage.warning(`第${key}轮独立区域名「${n}」重复, 请改名`); return; }
        ns.add(n);
      }
      if (valid.length) overrides[key] = valid.map(g => ({ ...g, name: String(g.name).trim() }));
    }
    r.rounds.region_overrides = overrides;
  }
  // 只保留画完整的区域, name 去掉首尾空格
  r.regions = validRegions.map(g => ({ ...g, name: String(g.name).trim() }));
  emit('save', JSON.parse(JSON.stringify(r)));
};
</script>
