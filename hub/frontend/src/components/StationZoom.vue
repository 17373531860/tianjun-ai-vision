<template>
  <!-- 单格放大覆盖层 (NVR 双击放大惯例): 提帧到 2fps, Esc/点罩关闭 -->
  <div class="zoom-mask" data-test="zoom-overlay" @click.self="emit('close')">
    <div class="zoom-panel">
      <header class="zoom-head">
        <span class="dot" :class="offline ? 'offline' : 'online'" />
        <span class="title" data-test="zoom-title">
          {{ nodeName }} · {{ station.display_name || `工位${station.channel_id}` }}
        </span>
        <span class="sub">{{ station.reported.logic_mode || '' }}</span>
        <span v-if="offline" class="hub-badge ng">节点离线</span>
        <span v-else class="state">{{ detecting ? '检测中' : '待机' }}</span>
        <button class="hub-link" data-test="zoom-enter" @click="enter">进入工位 ›</button>
        <button class="hub-link muted" data-test="zoom-close"
                @click="emit('close')">关闭 (Esc)</button>
      </header>
      <div class="zoom-frame" :class="{ dim: offline || broken }">
        <img v-if="src" :src="src" alt="" />
        <div v-else class="placeholder">画面加载中…</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSnapshot } from '../composables/useSnapshot'

const props = defineProps({
  nodeId: { type: Number, required: true },
  station: { type: Object, required: true },
  nodeName: { type: String, default: '' },
  offline: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])
const router = useRouter()

const detecting = computed(() => !!props.station.reported.detecting)
// 放大层提帧: 500ms (总览 1fps / 放大 2fps 分层惯例, Frigate/NVR 同款)
const { src, broken } = useSnapshot(
  () => `/nodes/${props.nodeId}/stations/${props.station.channel_id}/snapshot`,
  500,
)

function enter() {
  router.push({
    name: 'station',
    params: { nodeId: props.nodeId, channelId: props.station.channel_id },
  })
}
function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.zoom-mask {
  position: fixed; inset: 0; z-index: 60;
  background: rgba(0, 0, 0, 0.72);
  display: flex; align-items: center; justify-content: center;
}
.zoom-panel {
  width: min(1280px, 92vw);
  background: var(--hub-panel); border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius-lg); overflow: hidden;
  display: flex; flex-direction: column;
}
.zoom-head {
  display: flex; align-items: center; gap: 12px; padding: 10px 16px;
}
.title { color: var(--hub-text); font-weight: 600; }
.sub { color: var(--hub-text-4); font-size: 12px; }
.state { color: var(--hub-text-3); font-size: 12px; }
.zoom-head .hub-link { margin-left: auto; }
.zoom-head .hub-link + .hub-link { margin-left: 0; }
.zoom-head .muted { color: var(--hub-text-3); }
.dot.offline { background: var(--hub-ng-solid); }
.zoom-frame {
  background: #000; aspect-ratio: 16/9; max-height: 78vh;
  display: flex; align-items: center; justify-content: center;
}
.zoom-frame img {
  width: 100%; height: 100%; object-fit: contain; display: block;
}
.zoom-frame.dim img { filter: grayscale(1) brightness(0.55); }
.placeholder { color: var(--hub-text-4); font-size: 14px; }
</style>
