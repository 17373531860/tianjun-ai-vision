<template>
  <router-link
    class="tile" :class="{ dim: offline || broken }"
    :to="{ name: 'station', params: { nodeId, channelId: station.channel_id } }"
    :data-test="`station-tile-${nodeId}-${station.channel_id}`"
  >
    <div class="frame">
      <img v-if="src" :src="src" alt="" />
      <div v-else class="placeholder">画面加载中…</div>
      <div v-if="offline || broken" class="stale-mask">
        {{ offline ? '节点离线' : '画面中断' }}
      </div>
      <span class="badge" :class="detecting ? 'on' : 'off'">
        {{ detecting ? '检测中' : '待机' }}
      </span>
    </div>
    <div class="meta">
      <span class="name">{{ station.display_name || `工位${station.channel_id}` }}</span>
      <span class="mode">{{ station.reported.logic_mode || '—' }}</span>
    </div>
  </router-link>
</template>

<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../composables/useSnapshot'

const props = defineProps({
  nodeId: { type: Number, required: true },
  station: { type: Object, required: true },
  offline: { type: Boolean, default: false },
})

const detecting = computed(() => !!props.station.reported.detecting)
const { src, broken } = useSnapshot(
  () => `/nodes/${props.nodeId}/stations/${props.station.channel_id}/snapshot`,
  1000,
)
</script>

<style scoped>
.tile {
  display: block; border-radius: var(--hub-radius-lg); overflow: hidden;
  background: var(--hub-bg); border: 1px solid var(--hub-border);
  transition: border-color 0.15s;
}
.tile:hover { border-color: var(--hub-primary); }
.tile.dim .frame img { filter: grayscale(1) brightness(0.55); }
.frame { position: relative; aspect-ratio: 16/9; background: #000; }
.frame img { width: 100%; height: 100%; object-fit: cover; display: block; }
.placeholder {
  height: 100%; display: flex; align-items: center; justify-content: center;
  color: var(--hub-text-4); font-size: 13px;
}
.stale-mask {
  position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; color: var(--hub-ng); background: rgba(0, 0, 0, .6);
  font-size: 13px;
}
/* ISA-101: 检测中是期望常态, 不上绿; 用中性徽标+左侧活动细条表达"活着" */
.badge {
  position: absolute; top: 8px; left: 8px; padding: 2px 8px 2px 10px;
  border-radius: var(--hub-radius-sm); font-size: 12px;
  background: rgba(15, 23, 42, 0.82); border: 1px solid var(--hub-border);
}
.badge.on {
  color: var(--hub-text);
  box-shadow: inset 2px 0 0 var(--hub-text-2);
}
.badge.off { color: var(--hub-text-3); }
.meta {
  display: flex; justify-content: space-between; align-items: center;
  gap: 8px; padding: 8px 10px;
}
.name {
  color: var(--hub-text); font-weight: 500; font-size: 13px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.mode { color: var(--hub-text-3); font-size: 12px; flex-shrink: 0; }
</style>
