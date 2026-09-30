import { useSystemStore } from '@/store/useSystemStore';
import { getModuleFontScale } from '@/utils/monitorModuleFonts';

export function useMonitorModuleFonts() {
  const store = useSystemStore();
  // 在渲染/watch 内读取响应式设置，滑块变化无需重新装载检测主页。
  const moduleFontScale = (key) => getModuleFontScale(store.display.monitor.moduleFontScale, key);
  const moduleFontStyle = (key) => ({ '--tj-module-scale': moduleFontScale(key) });
  return { moduleFontScale, moduleFontStyle };
}
