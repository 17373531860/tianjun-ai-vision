<template>
  <!-- 在途报警持续横幅: 外部生产管控系统已收到的报警, 在中控回推"消除命令"前一直挂在屏上.
       teleport 到 body, 浮在所有布局之上, 不受 Monitor 双工位/插件覆盖布局影响.
       惰性: 后端没配报警台账 → active-alarms 永远空 → 横幅永不出现 (零打扰).
       外观/行为 (开关/位置/颜色/轮询/字段) 全部读后端入站配置 alarm_banner, 客户可在 UI 自配. -->
  <teleport to="body">
    <transition name="ext-alarm-slide">
      <div
        v-if="banner.enabled && alarms.length"
        class="ext-alarm-banner"
        :class="banner.position === 'bottom' ? 'pos-bottom' : 'pos-top'"
        :style="bannerStyle"
      >
        <div class="ext-alarm-head">
          <span class="ext-alarm-dot"></span>
          <span class="ext-alarm-title">报警待消除 · {{ alarms.length }} 条</span>
          <span class="ext-alarm-sub">等待生产管控系统消除</span>
        </div>
        <div class="ext-alarm-list">
          <div v-for="a in alarms" :key="a.id" class="ext-alarm-item">
            <div class="ext-alarm-text">{{ a.warning_text || '检测报警' }}</div>
            <div class="ext-alarm-meta">
              <span v-if="banner.show_task_no && a.task_no">任务 {{ a.task_no }}</span>
              <span v-if="banner.show_product_code && a.product_code">产品 {{ a.product_code }}</span>
              <span v-if="banner.show_step_code && a.step_code">工步 {{ a.step_code }}</span>
              <span v-if="banner.show_operator && a.operator">操作员 {{ a.operator }}</span>
              <span v-if="banner.show_time && a.raised_at" class="ext-alarm-time">{{ fmtTime(a.raised_at) }}</span>
            </div>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue';
import { getActiveAlarms, getInboundConfig } from '@/api/gateway';

const props = defineProps({
  // 指定只看某工位的报警; 不传 = 全部工位 (单线场景推荐全部, 不漏报)。
  channelId: { type: Number, default: null },
});

const alarms = ref([]);
// 横幅外观/行为默认值; 挂载后用后端 alarm_banner 覆盖。
const banner = reactive({
  enabled: true,
  position: 'top',
  color: '#dc2626',
  poll_interval_sec: 3,
  show_task_no: true,
  show_product_code: true,
  show_step_code: true,
  show_operator: true,
  show_time: true,
});

let timer = null;
let configTimer = null;
let stopped = false;
// 横幅配置热刷新节拍(ms): 客户在配置页改了外观/轮询/字段, 无需重进监控页即可生效
const CONFIG_REFRESH_MS = 15000;

const bannerStyle = computed(() => ({
  background: `linear-gradient(135deg, ${banner.color}, ${banner.color})`,
  boxShadow: `0 8px 28px ${hexToRgba(banner.color, 0.45)}`,
}));

function hexToRgba(hex, alpha) {
  try {
    let h = String(hex || '#dc2626').replace('#', '');
    if (h.length === 3) h = h.split('').map((c) => c + c).join('');
    const r = parseInt(h.substring(0, 2), 16);
    const g = parseInt(h.substring(2, 4), 16);
    const b = parseInt(h.substring(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  } catch (e) {
    return `rgba(220, 38, 38, ${alpha})`;
  }
}

function unwrap(res) {
  if (res == null) return null;
  return res.data !== undefined ? res.data : res;
}

async function loadBannerConfig() {
  try {
    const cfg = unwrap(await getInboundConfig()) || {};
    const b = cfg.alarm_banner || {};
    banner.enabled = b.enabled !== false;
    banner.position = b.position || 'top';
    banner.color = b.color || '#dc2626';
    banner.poll_interval_sec = b.poll_interval_sec ?? 3;
    banner.show_task_no = b.show_task_no !== false;
    banner.show_product_code = b.show_product_code !== false;
    banner.show_step_code = b.show_step_code !== false;
    banner.show_operator = b.show_operator !== false;
    banner.show_time = b.show_time !== false;
  } catch (e) {
    // 静默: 后端未就绪时用默认外观
  }
}

async function poll() {
  if (!banner.enabled) return;
  try {
    const params = props.channelId != null ? { channel_id: props.channelId } : {};
    const data = unwrap(await getActiveAlarms(params));
    alarms.value = Array.isArray(data) ? data : (data?.items || []);
  } catch (e) {
    // 静默: 后端未就绪/无该端点时不刷错, 保持上一帧
  }
}

function fmtTime(iso) {
  try {
    const d = new Date(iso);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  } catch (e) {
    return '';
  }
}

function startTimer() {
  if (timer) clearInterval(timer);
  const ms = Math.max(1, Number(banner.poll_interval_sec) || 3) * 1000;
  timer = setInterval(() => { if (!stopped) poll(); }, ms);
}

// 周期性重载配置; 仅当轮询间隔变了才重建轮询定时器(避免无谓重置相位)
async function refreshConfig() {
  if (stopped) return;
  const prevInterval = banner.poll_interval_sec;
  await loadBannerConfig();
  if (banner.poll_interval_sec !== prevInterval) startTimer();
}

onMounted(async () => {
  await loadBannerConfig();
  poll();
  startTimer();
  configTimer = setInterval(refreshConfig, CONFIG_REFRESH_MS);
});

onUnmounted(() => {
  stopped = true;
  if (timer) clearInterval(timer);
  if (configTimer) clearInterval(configTimer);
});
</script>

<style scoped>
.ext-alarm-banner {
  position: fixed;
  left: 50%;
  transform: translateX(-50%);
  z-index: 3000;
  max-width: 90vw;
  min-width: 360px;
  padding: 10px 16px;
  border-radius: 12px;
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.25);
}
.ext-alarm-banner.pos-top {
  top: 0;
  margin-top: 8px;
}
.ext-alarm-banner.pos-bottom {
  bottom: 0;
  margin-bottom: 8px;
}
.ext-alarm-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 15px;
}
.ext-alarm-sub {
  font-weight: 400;
  font-size: 12px;
  opacity: 0.85;
  margin-left: auto;
}
.ext-alarm-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.7);
  animation: ext-alarm-pulse 1.2s infinite;
}
@keyframes ext-alarm-pulse {
  0% { box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.7); }
  70% { box-shadow: 0 0 0 8px rgba(255, 255, 255, 0); }
  100% { box-shadow: 0 0 0 0 rgba(255, 255, 255, 0); }
}
.ext-alarm-list {
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 40vh;
  overflow-y: auto;
}
.ext-alarm-item {
  padding: 6px 10px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.18);
}
.ext-alarm-text {
  font-size: 14px;
  font-weight: 600;
}
.ext-alarm-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  opacity: 0.9;
  margin-top: 2px;
}
.ext-alarm-time {
  margin-left: auto;
  opacity: 0.8;
}
.ext-alarm-slide-enter-active,
.ext-alarm-slide-leave-active {
  transition: all 0.3s ease;
}
.ext-alarm-slide-enter-from,
.ext-alarm-slide-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-16px);
}
</style>
