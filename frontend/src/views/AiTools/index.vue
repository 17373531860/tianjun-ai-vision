<template>
  <div class="p-6 space-y-6 overflow-y-auto h-full">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-xl font-bold text-white">AI 能力试用</h1>
        <p class="text-gray-400 text-sm mt-1">
          OCR 读字（序列号/铭牌/工单）、异常检测（只学合格品的免缺陷样本质检）与 VLM 坐诊（看图问答）——上传图片或直接对通道当前画面试用，谈单当场演示
        </p>
      </div>
    </div>

    <!-- ==================== OCR 读字 ==================== -->
    <div class="bg-ind-panel rounded-lg border border-gray-800 p-5 space-y-4">
      <div class="flex items-center gap-3">
        <h2 class="text-lg font-semibold text-white">OCR 读字</h2>
        <el-tag v-if="ocrStatus" :type="ocrStatus.available ? 'success' : 'danger'" size="small" effect="dark">
          {{ ocrStatus.available ? '引擎可用' : '引擎不可用' }}
        </el-tag>
        <span v-if="ocrStatus && !ocrStatus.available" class="text-xs text-red-400">{{ ocrStatus.error || '依赖未安装 (rapidocr_onnxruntime)' }}</span>
      </div>

      <div class="flex items-center gap-3 flex-wrap">
        <el-upload :show-file-list="false" :auto-upload="false" accept="image/*" :on-change="onOcrFile">
          <el-button type="primary" plain :loading="ocrLoading" data-test="ocr-upload-btn">上传图片识别</el-button>
        </el-upload>
        <span class="text-gray-500 text-xs">或</span>
        <el-input-number v-model="ocrChannel" size="small" :min="0" :max="15" class="!w-24" />
        <el-button plain :loading="ocrLoading" @click="doOcrFrame" data-test="ocr-frame-btn">读通道当前画面</el-button>
      </div>

      <div v-if="ocrResult" data-test="ocr-result">
        <div class="text-sm text-gray-300 mb-2">识别到 {{ ocrResult.count }} 段文字，耗时后端处理：</div>
        <div v-if="ocrResult.count" class="bg-slate-900 rounded p-3 space-y-1 max-h-64 overflow-y-auto">
          <div v-for="(r, i) in ocrResult.results" :key="i" class="flex items-center gap-3 text-sm">
            <span class="text-white font-mono">{{ r.text }}</span>
            <el-tag size="small" type="info">{{ (r.score * 100).toFixed(1) }}%</el-tag>
          </div>
        </div>
        <div v-else class="text-gray-500 text-sm">未识别到文字</div>
      </div>
    </div>

    <!-- ==================== 异常检测 ==================== -->
    <div class="bg-ind-panel rounded-lg border border-gray-800 p-5 space-y-4">
      <div class="flex items-center gap-3">
        <h2 class="text-lg font-semibold text-white">异常检测（合格品记忆库）</h2>
        <span class="text-xs text-gray-500">拿一批合格品图建库，缺陷不用标注：新图偏离合格分布即报异常</span>
      </div>

      <!-- 建库 -->
      <div class="flex items-center gap-3 flex-wrap bg-slate-900 p-3 rounded border border-slate-700">
        <el-input v-model="newBankName" size="small" placeholder="记忆库名称（如 端盖A面）" class="!w-52" data-test="bank-name-input" />
        <el-upload :show-file-list="false" :auto-upload="false" accept="image/*" multiple :on-change="onBankFile">
          <el-button size="small" plain>选择合格品图片</el-button>
        </el-upload>
        <span v-if="bankFiles.length" class="text-xs text-emerald-400">已选 {{ bankFiles.length }} 张</span>
        <el-button size="small" type="primary" :disabled="!bankFiles.length" :loading="bankCreating"
          @click="doCreateBank" data-test="bank-create-btn">建库</el-button>
      </div>

      <!-- 库列表 -->
      <el-table :data="banks" size="small" class="w-full" empty-text="暂无记忆库，先用合格品图片建库">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="num_images" label="样本数" width="80" />
        <el-table-column prop="num_vectors" label="特征数" width="90" />
        <el-table-column label="判定阈值" width="180">
          <template #default="{ row }">
            <el-input-number :model-value="row.threshold" size="small" :min="0.0001" :step="0.1" class="!w-32"
              @change="(v) => doUpdateThreshold(row, v)" />
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="建库时间" width="160" />
        <el-table-column label="试用" width="230">
          <template #default="{ row }">
            <el-upload :show-file-list="false" :auto-upload="false" accept="image/*" class="inline-block mr-2"
              :on-change="(f) => doScoreImage(row, f)">
              <el-button size="small" plain>上传评分</el-button>
            </el-upload>
            <el-button size="small" plain @click="doScoreFrame(row)">评当前画面</el-button>
          </template>
        </el-table-column>
        <el-table-column label="" width="70">
          <template #default="{ row }">
            <el-button size="small" link type="danger" @click="doDeleteBank(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 评分结果 -->
      <div v-if="scoreResult" class="bg-slate-900 rounded p-4 flex items-start gap-6" data-test="anomaly-result">
        <div class="space-y-1 text-sm">
          <div class="flex items-center gap-2">
            <el-tag :type="scoreResult.is_anomaly ? 'danger' : 'success'" effect="dark">
              {{ scoreResult.is_anomaly ? '异常' : '正常' }}
            </el-tag>
            <span class="text-gray-400">库：{{ scoreResult._bankName }}</span>
          </div>
          <div class="text-gray-300">异常分数：<span class="text-white font-mono">{{ scoreResult.score }}</span></div>
          <div class="text-gray-300">判定阈值：<span class="text-white font-mono">{{ scoreResult.threshold }}</span></div>
          <div class="text-gray-500 text-xs">分数 = 最偏离合格分布的局部区域距离；热力图亮处 = 可疑位置</div>
        </div>
        <canvas v-if="scoreResult.heatmap" ref="heatmapCanvas" width="168" height="168"
          class="rounded border border-slate-700" title="异常热力图（亮=可疑）"></canvas>
      </div>
    </div>

    <!-- ==================== VLM 坐诊 ==================== -->
    <div class="bg-ind-panel rounded-lg border border-gray-800 p-5 space-y-4">
      <div class="flex items-center gap-3 flex-wrap">
        <h2 class="text-lg font-semibold text-white">VLM 坐诊（看图问答）</h2>
        <el-tag v-if="vlmCfg" :type="vlmCfg.enabled ? 'success' : 'info'" size="small" effect="dark" data-test="vlm-status-tag">
          {{ vlmCfg.enabled ? '已启用' : '未启用' }}
        </el-tag>
        <span class="text-xs text-gray-500">
          实时引擎站岗快筛，大模型只看关键帧回答"看懂"类问题（面向哪块面板/缺陷是什么/门开没开）。默认关闭零开销
        </span>
      </div>

      <!-- 配置 -->
      <div class="flex items-center gap-3 flex-wrap bg-slate-900 p-3 rounded border border-slate-700 text-xs">
        <el-switch v-model="vlmForm.enabled" active-text="启用" data-test="vlm-enable-switch" />
        <el-input v-model="vlmForm.endpoint" size="small" class="!w-72"
          placeholder="OpenAI 兼容端点，如 http://127.0.0.1:11434/v1" />
        <el-input v-model="vlmForm.model" size="small" class="!w-48" placeholder="模型名，如 qwen2.5-vl:7b" />
        <el-input v-model="vlmForm.api_key" size="small" class="!w-40" placeholder="API Key（本地可留空）" show-password />
        <el-button size="small" type="primary" plain :loading="vlmSaving" @click="doSaveVlm" data-test="vlm-save-btn">保存</el-button>
        <el-button size="small" plain @click="doProbeVlm">测试连接</el-button>
        <span v-if="vlmProbe" :class="vlmProbe.reachable ? 'text-emerald-400' : 'text-red-400'">
          {{ vlmProbe.reachable ? '端点可达' : `不可达: ${vlmProbe.detail}` }}
        </span>
        <span class="text-gray-500 w-full">推荐本地 Ollama + Qwen2.5-VL-7B（Apache 2.0 可商用可离线）；3B 版非商用、72B 版有用户量门槛条款，选型请钉死 7B</span>
      </div>

      <!-- 问答 -->
      <div class="flex items-center gap-3 flex-wrap">
        <el-input v-model="vlmQuestion" size="small" class="!w-96" placeholder="问题，如：这个人面向哪块面板？画面里有什么异常？"
          data-test="vlm-question-input" />
        <el-upload :show-file-list="false" :auto-upload="false" accept="image/*" :on-change="onVlmFile">
          <el-button plain type="primary" :loading="vlmAsking" :disabled="!vlmQuestion" data-test="vlm-upload-btn">传图提问</el-button>
        </el-upload>
        <span class="text-gray-500 text-xs">或</span>
        <el-input-number v-model="vlmChannel" size="small" :min="0" :max="15" class="!w-24" />
        <el-button plain :loading="vlmAsking" :disabled="!vlmQuestion" @click="doVlmFrame" data-test="vlm-frame-btn">问通道当前画面</el-button>
      </div>

      <div v-if="vlmAnswer" class="bg-slate-900 rounded p-4 space-y-2" data-test="vlm-answer">
        <div class="text-xs text-gray-500">{{ vlmAnswer.model }} · {{ vlmAnswer.latency_ms }}ms</div>
        <div class="text-sm text-gray-200 whitespace-pre-wrap">{{ vlmAnswer.answer }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  createAnomalyBank, deleteAnomalyBank, getOcrStatus, getVlmConfig, getVlmStatus,
  listAnomalyBanks, ocrReadFrame, ocrReadImage, saveVlmConfig, scoreAnomalyFrame,
  scoreAnomalyImage, updateAnomalyThreshold, vlmAskFrame, vlmAskImage,
} from '@/api/aitools';

// ---------- OCR ----------
const ocrStatus = ref(null);
const ocrLoading = ref(false);
const ocrResult = ref(null);
const ocrChannel = ref(0);

const onOcrFile = async (uploadFile) => {
  if (!uploadFile?.raw) return;
  ocrLoading.value = true;
  try {
    ocrResult.value = await ocrReadImage(uploadFile.raw);
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || 'OCR 识别失败');
  } finally {
    ocrLoading.value = false;
  }
};

const doOcrFrame = async () => {
  ocrLoading.value = true;
  try {
    ocrResult.value = await ocrReadFrame(ocrChannel.value);
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '读取通道画面失败（视频源未运行？）');
  } finally {
    ocrLoading.value = false;
  }
};

// ---------- 异常检测 ----------
const banks = ref([]);
const newBankName = ref('');
const bankFiles = ref([]);
const bankCreating = ref(false);
const scoreResult = ref(null);
const heatmapCanvas = ref(null);

const refreshBanks = async () => {
  try {
    banks.value = (await listAnomalyBanks()).banks || [];
  } catch { /* 列表失败静默, 表格空态自解释 */ }
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

const _renderHeatmap = async () => {
  await nextTick();
  const hm = scoreResult.value?.heatmap;
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

const _showScore = (row, res) => {
  scoreResult.value = { ...res, _bankName: row.name };
  _renderHeatmap();
};

const doScoreImage = async (row, uploadFile) => {
  if (!uploadFile?.raw) return;
  try {
    _showScore(row, await scoreAnomalyImage(row.id, uploadFile.raw));
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '评分失败');
  }
};

const doScoreFrame = async (row) => {
  try {
    _showScore(row, await scoreAnomalyFrame(row.id));
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '读取通道画面失败（视频源未运行？）');
  }
};

// ---------- VLM 坐诊 ----------
const vlmCfg = ref(null);
const vlmForm = ref({ enabled: false, endpoint: '', model: '', api_key: '' });
const vlmSaving = ref(false);
const vlmProbe = ref(null);
const vlmQuestion = ref('');
const vlmChannel = ref(0);
const vlmAsking = ref(false);
const vlmAnswer = ref(null);

const refreshVlm = async () => {
  try {
    vlmCfg.value = await getVlmConfig();
    vlmForm.value = {
      enabled: !!vlmCfg.value.enabled,
      endpoint: vlmCfg.value.endpoint || '',
      model: vlmCfg.value.model || '',
      api_key: vlmCfg.value.api_key || '',
    };
  } catch { /* 探针失败不阻塞页面 */ }
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

const onVlmFile = async (uploadFile) => {
  if (!uploadFile?.raw || !vlmQuestion.value) return;
  vlmAsking.value = true;
  try {
    vlmAnswer.value = await vlmAskImage(uploadFile.raw, vlmQuestion.value);
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || 'VLM 问答失败');
  } finally {
    vlmAsking.value = false;
  }
};

const doVlmFrame = async () => {
  if (!vlmQuestion.value) return;
  vlmAsking.value = true;
  try {
    vlmAnswer.value = await vlmAskFrame(vlmChannel.value, vlmQuestion.value);
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '读取通道画面失败（视频源未运行？或 VLM 未启用）');
  } finally {
    vlmAsking.value = false;
  }
};

onMounted(async () => {
  try {
    ocrStatus.value = await getOcrStatus();
  } catch { /* 探针失败不阻塞页面 */ }
  await refreshBanks();
  await refreshVlm();
});
</script>
