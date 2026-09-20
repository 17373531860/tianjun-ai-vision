import { computed } from 'vue';
import { useRoute } from 'vue-router';

// 布局与操作权限分开：只有手部裁切窗去掉宿主导航，工位窗始终沿用主屏布局。
export function useDisplayWindow() {
  const route = useRoute();
  const handsOnly = computed(() => route.query.kiosk === '1'
    && route.query.video_only === '1' && route.query.hands_crop === '1');
  const station = computed(() => !handsOnly.value
    && (route.query.kiosk === '1' || route.query.station_view === '1'));
  const readonly = computed(() => handsOnly.value || (station.value && route.query.readonly !== '0'));
  const managementReadonly = computed(() => route.query.kiosk === '1' || readonly.value);
  const channel = computed(() => Math.max(0, Number.parseInt(route.query.channel, 10) || 0));
  return { handsOnly, station, readonly, managementReadonly, channel };
}
