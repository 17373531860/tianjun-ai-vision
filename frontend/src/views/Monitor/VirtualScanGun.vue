<template>
  <div class="bg-slate-900 border border-slate-700 rounded-lg overflow-hidden">
    <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex items-center justify-between cursor-pointer"
         @click="expanded = !expanded">
      <span class="text-cyan-400 text-base font-bold">虚拟扫码枪测试台</span>
      <div class="flex items-center gap-2">
        <span class="text-[0.625rem] text-gray-500">无硬件时模拟扫码（软件按规则补连字符后拉单）</span>
        <span class="text-gray-400 text-xs">{{ expanded ? '收起 ▲' : '展开 ▼' }}</span>
      </div>
    </div>

    <div v-show="expanded" class="p-3 space-y-3">
      <div class="flex items-center gap-2">
        <input v-model="rawCode"
               @keyup.enter="doScan"
               placeholder="输入序列号（去掉 - 的原始扫码值，如 JOB1503000213）"
               class="flex-1 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm text-white
                      placeholder-gray-500 focus:border-cyan-500 outline-none" />
        <button @click="doScan" :disabled="!rawCode || busy"
                class="bg-cyan-600 hover:bg-cyan-500 disabled:bg-slate-700 text-white px-4 py-1 rounded
                       text-sm font-bold transition-colors">
          扫一下
        </button>
      </div>

      <div>
        <div class="text-[0.625rem] text-gray-500 mb-1">常用工单（点击即扫，软件会补 -）</div>
        <div class="flex flex-wrap gap-1.5">
          <button v-for="p in presets" :key="p"
                  @click="rawCode = p; doScan()"
                  class="bg-slate-800 hover:bg-cyan-700 border border-slate-600 text-gray-300
                         px-2 py-0.5 rounded text-[0.7rem] font-mono transition-colors">
            {{ p }}
          </button>
        </div>
      </div>

      <div v-if="lastResult" class="text-xs rounded px-2 py-1.5 border"
           :class="lastResult.ok ? 'bg-emerald-900/40 border-emerald-700 text-emerald-200'
                                 : 'bg-rose-900/40 border-rose-700 text-rose-200'">
        {{ lastResult.msg }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { packagingScan } from '@/api/packaging_flow';

const props = defineProps({
  channelId: { type: Number, default: 0 },
});

const expanded = ref(false);
const rawCode = ref('');
const busy = ref(false);
const lastResult = ref(null);

// 上银 SY 9 工单（原始扫码值，无连字符；软件补回第 12 位的 -）
const presets = [
  'JOB1503000213', 'JOB15030002131', 'JOB150300021313',
  'JOB2026051321', 'JOB20260513212', 'JOB202605132123',
  'JOB2024062212', 'JOB202406221213',
];

async function doScan() {
  const code = (rawCode.value || '').trim();
  if (!code || busy.value) return;
  busy.value = true;
  try {
    const { data } = await packagingScan({ code, channel_id: props.channelId });
    const st = data && data.state;
    if (st && st.order_no) {
      lastResult.value = {
        ok: true,
        msg: `已拉单：${st.order_no}` +
             (st.cust_name ? `　客户：${st.cust_name}` : '') +
             (st.box_total ? `　应做 ${st.box_total} 箱` : '') +
             (st.slider_total ? `　滑块总数 ${st.slider_total}` : ''),
      };
    } else {
      lastResult.value = { ok: false, msg: data && data.message ? data.message : '未开工单（查无此单或被阻断）' };
    }
  } catch (e) {
    lastResult.value = { ok: false, msg: '扫码失败：' + (e.response?.data?.detail || e.message || '未知错误') };
  } finally {
    busy.value = false;
  }
}
</script>
