<template>
  <!-- 能力试用抽屉 (2026-09 内置能力模型入仓): 原「AI 能力试用」页全量迁入并下线该页。
       按模型 capability 渲染对应面板; 后端 /ocr /anomaly /vlm /orientation 端点原样复用。
       除传图试用外, 还承接旧页三块管理/标定功能:
       - 各能力「读通道当前画面」试用 (朝向装机标定: 工程师站到点位实测角度抄进规则)
       - 异常检测记忆库管理 (建库/阈值/删库)
       - VLM 连接配置 (端点/模型名/API Key/探活) -->
  <el-drawer v-model="visible" :title="title" size="520px" destroy-on-close>
    <div v-if="model" class="space-y-4 px-1" data-test="cap-try-drawer">
      <div class="text-xs text-gray-500">
        {{ capMeta.value || '' }}
      </div>

      <!-- VLM: 连接配置 (原 AI 试用页配置块迁入, /vlm/config 落库) -->
      <div v-if="cap === 'vlm'" class="bg-gray-900/60 rounded p-3 space-y-2" data-test="cap-vlm-config">
        <div class="flex items-center gap-3">
          <span class="text-sm text-gray-300 font-medium">连接配置</span>
          <el-switch v-model="vlmForm.enabled" active-text="启用" size="small" data-test="vlm-enable-switch" />
          <el-tag v-if="vlmCfg" :type="vlmCfg.enabled ? 'success' : 'info'" size="small" effect="dark" data-test="vlm-status-tag">
            {{ vlmCfg.enabled ? '已启用' : '未启用' }}
          </el-tag>
        </div>
        <el-input v-model="vlmForm.endpoint" size="small" placeholder="OpenAI 兼容端点，如 http://127.0.0.1:11434/v1" />
        <div class="flex gap-2">
          <el-input v-model="vlmForm.model" size="small" placeholder="模型名，如 qwen2.5-vl:7b" />
          <el-input v-model="vlmForm.api_key" size="small" placeholder="API Key（本地可留空）" show-password />
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          <el-button size="small" type="primary" plain :loading="vlmSaving" data-test="vlm-save-btn" @click="doSaveVlm">保存配置</el-button>
          <el-button size="small" plain @click="doProbeVlm">测试连接</el-button>
          <span v-if="vlmProbe" class="text-xs" :class="vlmProbe.reachable ? 'text-emerald-400' : 'text-red-400'">
            {{ vlmProbe.reachable ? '端点可达' : `不可达: ${vlmProbe.detail}` }}
          </span>
        </div>
        <div class="text-xs text-gray-500">
          推荐本地 Ollama + Qwen2.5-VL-7B（Apache 2.0 可商用可离线）；3B 版非商用、72B 版有用户量门槛条款，选型请钉死 7B
        </div>
      </div>

      <!-- 异常检测: 记忆库管理 (原 AI 试用页建库/阈值/删库迁入) -->
      <div v-if="cap === 'anomaly'" class="bg-gray-900/60 rounded p-3 space-y-2" data-test="cap-bank-manage">
        <div class="text-sm text-gray-300 font-medium">记忆库管理</div>
        <div class="text-xs text-gray-500">拿 5~50 张合格品图建库，缺陷不用标注：新图偏离合格分布即报异常</div>
        <div class="flex items-center gap-2 flex-wrap">
          <el-input v-model="newBankName" size="small" placeholder="记忆库名称（如 端盖A面）" class="!w-44" data-test="bank-name-input" />
          <el-upload :show-file-list="false" :auto-upload="false" accept="image/*" multiple :on-change="onBankFile">
            <el-button size="small" plain>选合格品图</el-button>
          </el-upload>
          <span v-if="bankFiles.length" class="text-xs text-emerald-400">已选 {{ bankFiles.length }} 张</span>
          <el-button size="small" type="primary" :disabled="!bankFiles.length" :loading="bankCreating"
                     data-test="bank-create-btn" @click="doCreateBank">建库</el-button>
        </div>
        <div v-for="b in banks" :key="b.id" class="flex items-center gap-2 text-xs bg-gray-900 rounded px-2 py-1.5">
          <span class="text-gray-200 flex-1 truncate">{{ b.name }}（{{ b.num_images ?? b.image_count ?? '?' }} 张）</span>
          <span class="text-gray-500">阈值</span>
          <el-input-number :model-value="b.threshold" size="small" :min="0.0001" :step="0.1" class="!w-28"
                           @change="(v) => doUpdateThreshold(b, v)" />
          <el-button size="small" link type="danger" @click="doDeleteBank(b)">删除</el-button>
        </div>
      </div>

      <!-- 试用输入 -->
      <el-form label-position="top" size="small">
        <el-form-item v-if="cap === 'anomaly'" label="评分用记忆库">
          <el-select v-model="bankId" class="w-full" placeholder="选择记忆库（先在上方建库）" data-test="cap-try-bank">
            <el-option v-for="b in banks" :key="b.id" :label="`${b.name} (${b.num_images ?? b.image_count ?? '?'} 张)`" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="cap === 'vlm'" label="问题">
          <el-input v-model="question" placeholder="例如: 图里操作员在做什么?" data-test="cap-try-question" />
        </el-form-item>
      </el-form>

      <el-upload drag action="#" :auto-upload="false" :show-file-list="false"
                 :on-change="onFile" accept=".jpg,.jpeg,.png,.bmp">
        <div class="py-4 text-sm text-gray-400">
          <div v-if="!previewUrl">拖拽或点击选择测试图片</div>
          <img v-else :src="previewUrl" class="max-h-40 mx-auto rounded" />
        </div>
      </el-upload>

      <el-button type="primary" class="w-full" :loading="running"
                 :disabled="!file || paramMissing"
                 data-test="cap-try-run" @click="run">
        运行试用
      </el-button>

      <!-- 读通道当前画面 (装机标定/现场试用: 无需拷图, 直接对运行中通道取帧) -->
      <div class="flex items-center gap-2">
        <span class="text-xs text-gray-500">或读通道当前画面:</span>
        <el-input-number v-model="channelId" size="small" :min="0" :max="15" class="!w-24" data-test="cap-try-channel" />
        <el-button size="small" plain :loading="running" :disabled="paramMissing"
                   data-test="cap-try-frame-run" @click="runFrame">试当前画面</el-button>
      </div>

      <!-- 结果 -->
      <div v-if="error" class="text-sm text-red-400 whitespace-pre-wrap" data-test="cap-try-error">{{ error }}</div>
      <div v-else-if="result" class="space-y-3" data-test="cap-try-result">
        <!-- OCR: 文本列表 (后端字段 results) -->
        <template v-if="cap === 'ocr'">
          <div class="text-sm text-gray-300">识别到 {{ (result.results || []).length }} 条文本:</div>
          <div v-for="(t, i) in result.results || []" :key="i"
               class="flex justify-between text-sm bg-gray-900/60 rounded px-3 py-1.5">
            <span class="font-mono">{{ t.text }}</span>
            <span class="text-gray-500">{{ (t.score * 100).toFixed(0) }}%</span>
          </div>
        </template>

        <!-- 朝向: 整幅当人框的单结果 (后端字段 found/yaw_deg/head_yaw_deg) + 罗盘 -->
        <template v-else-if="cap === 'pose' || cap === 'headpose'">
          <div v-if="!result.found" class="text-sm text-gray-400">未检出可判定的人 (换一张有人的图试试)</div>
          <div v-else class="bg-gray-900/60 rounded p-3 flex items-center gap-4">
            <!-- 朝向罗盘: 图像平面角 0°=右 90°=下, 与 CSS rotate 同向 -->
            <svg width="72" height="72" viewBox="-36 -36 72 72" class="shrink-0">
              <circle r="33" fill="none" stroke="#334155" stroke-width="2" />
              <g :transform="`rotate(${result.yaw_deg})`">
                <line x1="-18" y1="0" x2="20" y2="0" stroke="#34d399" stroke-width="4" stroke-linecap="round" />
                <path d="M20 0 L10 -7 L10 7 Z" fill="#34d399" />
              </g>
              <g v-if="result.head_yaw_deg != null" :transform="`rotate(${result.head_yaw_deg})`">
                <line x1="0" y1="0" x2="26" y2="0" stroke="#fbbf24" stroke-width="2" stroke-dasharray="3 2" />
              </g>
            </svg>
            <div class="space-y-1 text-sm">
              <div class="text-gray-300">身体朝向 <span class="text-emerald-400 font-mono">{{ fmtDeg(result.yaw_deg) }}</span>
                <span v-if="result.head_yaw_deg != null" class="text-gray-300"> · 头部 <span class="text-amber-400 font-mono">{{ fmtDeg(result.head_yaw_deg) }}</span></span>
              </div>
              <div class="text-xs text-gray-500">
                conf {{ (result.conf ?? 0).toFixed(2) }} ·
                {{ result.facing_camera ? '面向相机' : '背对相机' }} ·
                {{ result.backend }}
              </div>
              <div class="text-xs text-gray-500">角度口径: 0°=画面右, 90°=画面下(面向相机), ±180°=画面左, -90°=画面上(背对)</div>
            </div>
          </div>
        </template>

        <!-- 异常: 得分 + 热力图 -->
        <template v-else-if="cap === 'anomaly'">
          <div class="bg-gray-900/60 rounded p-3 flex items-start gap-4">
            <div class="space-y-1">
              <div class="text-lg font-bold" :class="result.is_anomaly ? 'text-red-400' : 'text-green-400'">
                {{ result.is_anomaly ? '异常' : '正常' }} · score {{ (result.score ?? 0).toFixed(3) }}
                <span class="text-xs text-gray-500">(阈值 {{ (result.threshold ?? 0).toFixed(3) }})</span>
              </div>
              <div class="text-xs text-gray-500">分数 = 最偏离合格分布的局部区域距离；热力图亮处 = 可疑位置</div>
            </div>
            <canvas v-if="result.heatmap" ref="heatmapCanvas" width="140" height="140"
                    class="rounded border border-slate-700 shrink-0" title="异常热力图（亮=可疑）"></canvas>
          </div>
        </template>

        <!-- VLM: 回答 -->
        <template v-else-if="cap === 'vlm'">
          <div class="text-xs text-gray-500" v-if="result.model">{{ result.model }} · {{ result.latency_ms }}ms</div>
          <div class="text-sm bg-gray-900/60 rounded px-3 py-2 whitespace-pre-wrap">{{ result.answer }}</div>
        </template>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  ocrReadImage, ocrReadFrame,
  listAnomalyBanks, createAnomalyBank, deleteAnomalyBank, updateAnomalyThreshold,
  scoreAnomalyImage, scoreAnomalyFrame,
  vlmAskImage, vlmAskFrame, getVlmConfig, saveVlmConfig, getVlmStatus,
  orientationEstimateImage, orientationEstimateFrame,
} from '@/api/aitools';
import { dbg, dbgErr } from '@/utils/debug';

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  model: { type: Object, default: null },       // 模型行 (capability 决定面板形态)
  capMeta: { type: Object, default: () => ({}) }, // 能力目录条目 (label/value)
});
const emit = defineEmits(['update:modelValue']);

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
});
const cap = computed(() => props.model?.capability || 'detect');
const title = computed(() => `试一试 · ${props.model?.name || ''}`);

const file = ref(null);
const previewUrl = ref('');
const running = ref(false);
const result = ref(null);
const error = ref('');
const question = ref('');
const banks = ref([]);
const bankId = ref(null);
const channelId = ref(0);
const heatmapCanvas = ref(null);

// 缺参禁用: anomaly 无库 / vlm 无问题
const paramMissing = computed(() =>
  (cap.value === 'anomaly' && !bankId.value) || (cap.value === 'vlm' && !question.value));

// ---------- 记忆库管理 ----------
const newBankName = ref('');
const bankFiles = ref([]);
const bankCreating = ref(false);

const refreshBanks = async () => {
  try {
    const r = await listAnomalyBanks();
    banks.value = r.items || r.banks || [];
    if (banks.value.length && !banks.value.some(b => b.id === bankId.value)) {
      bankId.value = banks.value[0].id;
    }
  } catch (e) { dbgErr('model.try', '拉取记忆库', e); }
};

const onBankFile = (uploadFile) => {
  if (uploadFile?.raw) bankFiles.value.push(uploadFile.raw);
};

const doCreateBank = async () => {
  bankCreating.value = true;
  try {
    const meta = await createAnomalyBank(newBankName.value, bankFiles.value);
    ElMessage.success(`记忆库「${meta.name}」已建（${meta.num_images} 张样本，建议阈值 ${meta.threshold}）`);
    bankFiles.value = [];
    newBankName.value = '';
    await refreshBanks();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '建库失败');
  } finally {
    bankCreating.value = false;
  }
};

const doDeleteBank = async (row) => {
  try {
    await ElMessageBox.confirm(`删除记忆库「${row.name}」？`, '确认', { type: 'warning' });
  } catch { return; }
  await deleteAnomalyBank(row.id);
  await refreshBanks();
};

const doUpdateThreshold = async (row, v) => {
  if (!v || v <= 0) return;
  try {
    await updateAnomalyThreshold(row.id, v);
    ElMessage.success('阈值已更新');
    await refreshBanks();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '阈值更新失败');
  }
};

// ---------- VLM 配置 ----------
const vlmCfg = ref(null);
const vlmForm = ref({ enabled: false, endpoint: '', model: '', api_key: '' });
const vlmSaving = ref(false);
const vlmProbe = ref(null);

const refreshVlm = async () => {
  try {
    vlmCfg.value = await getVlmConfig();
    vlmForm.value = {
      enabled: !!vlmCfg.value.enabled,
      endpoint: vlmCfg.value.endpoint || '',
      model: vlmCfg.value.model || '',
      api_key: vlmCfg.value.api_key || '',
    };
  } catch (e) { dbgErr('model.try', '拉取 VLM 配置', e); }
};

const doSaveVlm = async () => {
  vlmSaving.value = true;
  try {
    vlmCfg.value = await saveVlmConfig(vlmForm.value);
    ElMessage.success('VLM 配置已保存');
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败');
  } finally {
    vlmSaving.value = false;
  }
};

const doProbeVlm = async () => {
  vlmProbe.value = null;
  try {
    vlmProbe.value = await getVlmStatus();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '探活失败');
  }
};

// ---------- 打开时初始化 ----------
watch(() => props.modelValue, async (v) => {
  if (!v) return;
  file.value = null; previewUrl.value = ''; result.value = null; error.value = '';
  vlmProbe.value = null;
  if (cap.value === 'anomaly') await refreshBanks();
  if (cap.value === 'vlm') await refreshVlm();
});

const onFile = (f) => {
  file.value = f.raw;
  previewUrl.value = URL.createObjectURL(f.raw);
  result.value = null; error.value = '';
};

const fmtDeg = (v) => (v == null ? '—' : `${Number(v).toFixed(1)}°`);

// 异常热力图渲染 (canvas 需等 v-if 挂载)
const renderHeatmap = async () => {
  await nextTick();
  const hm = result.value?.heatmap;
  const canvas = heatmapCanvas.value;
  if (!hm || !canvas) return;
  const ctx = canvas.getContext('2d');
  const n = hm.length;
  const cell = canvas.width / n;
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < hm[r].length; c++) {
      const v = hm[r][c]; // 0~1
      ctx.fillStyle = `rgb(${Math.round(255 * v)}, ${Math.round(64 * (1 - v))}, ${Math.round(160 * (1 - v))})`;
      ctx.fillRect(c * cell, r * cell, Math.ceil(cell), Math.ceil(cell));
    }
  }
};

const _finish = async (res) => {
  result.value = res;
  if (cap.value === 'anomaly') await renderHeatmap();
};

const run = async () => {
  if (!file.value) return;
  dbg('model.try', '运行能力试用(传图)', `cap=${cap.value} model=${props.model?.id}`);
  running.value = true; result.value = null; error.value = '';
  try {
    if (cap.value === 'ocr') {
      await _finish(await ocrReadImage(file.value));
    } else if (cap.value === 'pose' || cap.value === 'headpose') {
      await _finish(await orientationEstimateImage(file.value));
    } else if (cap.value === 'anomaly') {
      await _finish(await scoreAnomalyImage(bankId.value, file.value, { withHeatmap: true }));
    } else if (cap.value === 'vlm') {
      await _finish(await vlmAskImage(file.value, question.value));
    }
  } catch (e) {
    dbgErr('model.try', '能力试用', e);
    error.value = e?.response?.data?.detail || e.message || '试用失败';
  } finally {
    running.value = false;
  }
};

const runFrame = async () => {
  dbg('model.try', '运行能力试用(通道帧)', `cap=${cap.value} ch=${channelId.value}`);
  running.value = true; result.value = null; error.value = '';
  try {
    if (cap.value === 'ocr') {
      await _finish(await ocrReadFrame(channelId.value));
    } else if (cap.value === 'pose' || cap.value === 'headpose') {
      await _finish(await orientationEstimateFrame(channelId.value));
    } else if (cap.value === 'anomaly') {
      await _finish(await scoreAnomalyFrame(bankId.value, channelId.value, { withHeatmap: true }));
    } else if (cap.value === 'vlm') {
      await _finish(await vlmAskFrame(channelId.value, question.value));
    }
  } catch (e) {
    dbgErr('model.try', '能力试用(通道帧)', e);
    error.value = e?.response?.data?.detail || e.message || '读取通道画面失败（视频源未运行？）';
  } finally {
    running.value = false;
  }
};
</script>
