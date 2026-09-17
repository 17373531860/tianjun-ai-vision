<template>
  <div class="space-y-4" data-testid="lan-station-panel">
    <el-alert type="info" :closable="false" show-icon>
      <template #title>一拖多：一台工作站 + 每工位一台一体机</template>
      <template #default>
        <div class="text-xs leading-6">
          工作站负责取流、推理、周期结算与项目库；每个工位摆一台一体机，网线进交换机，
          用 Chrome / Edge 全屏打开下面对应的地址即可，<b>一体机不用装软件、不用配后端地址</b>。
          项目、模型、工位数、系统设置只在工作站（或管理电脑）上做，工位屏看不到这些入口。
        </div>
      </template>
    </el-alert>

    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <el-icon class="text-tech-blue"><Monitor /></el-icon>
            <span class="font-bold text-white">工位屏访问地址</span>
          </div>
          <el-button size="small" :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
        </div>
      </template>

      <div v-if="!isHttpOrigin" class="mb-3">
        <el-alert type="warning" :closable="false" show-icon
                  title="当前是桌面程序窗口，下面地址里的主机名需要换成工作站的局域网 IP" />
      </div>

      <el-table :data="rows" size="small" stripe>
        <el-table-column label="工位" width="90">
          <template #default="{ row }">
            <span class="font-bold text-white">工位 {{ row.channel + 1 }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作屏地址（可看画面 + 可启停）" min-width="330">
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <code class="flex-1 truncate rounded bg-slate-900 px-2 py-1 font-mono text-xs text-cyan-300"
                    :data-testid="`lan-station-url-${row.channel}`"
                    :title="row.operatorUrl">{{ row.operatorUrl }}</code>
              <el-button size="small" link type="primary" @click="copy(row.operatorUrl)">复制</el-button>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="手部裁切副屏" width="130" align="center">
          <template #default="{ row }">
            <el-switch
              v-model="row.handsAux"
              :loading="savingChannel === row.channel"
              :data-testid="`lan-station-hands-aux-${row.channel}`"
              @change="(val) => onToggleHandsAux(row, val)"
            />
          </template>
        </el-table-column>

        <el-table-column label="副屏地址（只显示手部裁切）" min-width="300">
          <template #default="{ row }">
            <div v-if="row.handsAux" class="flex items-center gap-2">
              <code class="flex-1 truncate rounded bg-slate-900 px-2 py-1 font-mono text-xs text-emerald-300"
                    :data-testid="`lan-station-hands-url-${row.channel}`"
                    :title="row.handsUrl">{{ row.handsUrl }}</code>
              <el-button size="small" link type="primary" @click="copy(row.handsUrl)">复制</el-button>
            </div>
            <span v-else class="text-xs text-slate-500">未启用</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="mt-3 space-y-1 text-xs text-slate-400">
        <div>· 操作屏按工位独占本工位直播；这一路被一体机看着时，工作站总览自动改用快照，不跟它抢同一路画面。</div>
        <div>· 同一个工位同时开两块操作屏时，后开的接管直播、先开的让位（沿用既有规则）。</div>
        <div>· 「手部裁切副屏」默认关：只有开了的工位才会算手部识别，其它工位一帧都不算，也不会把骨架画到工位主画面上。</div>
        <div>· 想让工位账号只能动自己那一路：系统设置 → 账号鉴权里建「工位屏」身份的账号，并填上「可操作工位」。</div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { Monitor, Refresh } from '@element-plus/icons-vue';
import { getHandsAuxConfig, getWorkstations, setHandsAuxEnabled } from '@/api/detection';

const loading = ref(false);
const savingChannel = ref(null);
const channelCount = ref(1);
const handsAux = ref({});

// 一体机要抄的是"能打通工作站的绝对地址"。浏览器里直接用当前 origin —— 局域网
// 打开本页时那就是工作站的真实地址；桌面壳里页面是 file://，没有可抄的 origin，
// 退回占位提示，由工程师换成工作站 IP。
const isHttpOrigin = computed(() => {
  const origin = window.location?.origin || '';
  return origin.startsWith('http');
});

const baseOrigin = computed(() => (
  isHttpOrigin.value ? window.location.origin : 'http://<工作站IP>:8001'
));

const rows = ref([]);

const buildRows = () => {
  rows.value = Array.from({ length: channelCount.value }, (_, ch) => ({
    channel: ch,
    handsAux: handsAux.value[String(ch)] === true,
    operatorUrl: `${baseOrigin.value}/#/monitor?channel=${ch}&kiosk=1&readonly=0`,
    handsUrl: `${baseOrigin.value}/#/monitor?channel=${ch}&kiosk=1&readonly=1`
      + '&video_only=1&hands_crop=1',
  }));
};

const reload = async () => {
  loading.value = true;
  try {
    const [ws, aux] = await Promise.all([getWorkstations(), getHandsAuxConfig()]);
    channelCount.value = Math.max(1, Number(ws?.data?.channel_count) || 1);
    handsAux.value = aux?.data?.channels || {};
    buildRows();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '读取工位屏配置失败');
  } finally {
    loading.value = false;
  }
};

const onToggleHandsAux = async (row, value) => {
  savingChannel.value = row.channel;
  try {
    const resp = await setHandsAuxEnabled(row.channel, value);
    handsAux.value = resp?.data?.channels || handsAux.value;
    buildRows();
    ElMessage.success(`工位 ${row.channel + 1} 手部裁切副屏已${value ? '开启' : '关闭'}`);
  } catch (e) {
    row.handsAux = !value;   // 写失败回弹，别让开关和后端不一致
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败');
  } finally {
    savingChannel.value = null;
  }
};

const copy = async (text) => {
  try {
    await navigator.clipboard.writeText(text);
    ElMessage.success('地址已复制');
  } catch {
    ElMessage.warning('浏览器不允许自动复制，请手动选中地址');
  }
};

onMounted(reload);
</script>
