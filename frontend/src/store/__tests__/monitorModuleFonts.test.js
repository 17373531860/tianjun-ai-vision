import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useSystemStore } from '../useSystemStore';
import { DEFAULT_MODULE_FONT_SCALE } from '@/utils/monitorModuleFonts';

vi.mock('@/api/project', () => ({ updateProject: vi.fn() }));

describe('检测主页分区字号设置恢复', () => {
  let savedSettings;

  beforeEach(() => {
    savedSettings = null;
    setActivePinia(createPinia());
    vi.stubGlobal('localStorage', {
      getItem: vi.fn((key) => key === 'display_settings' ? savedSettings : null),
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it('新安装的八个分区均保持 100%，默认对象不随 store 设置改变', () => {
    const store = useSystemStore();
    expect(Object.keys(store.display.monitor.moduleFontScale)).toHaveLength(8);
    expect(store.display.monitor.moduleFontScale).toEqual(DEFAULT_MODULE_FONT_SCALE);
    store.display.monitor.moduleFontScale.sop = 160;
    expect(DEFAULT_MODULE_FONT_SCALE.sop).toBe(100);
  });

  it('旧 localStorage 缺少分区字号字段时补齐默认值并保留原显示开关', () => {
    savedSettings = JSON.stringify({ monitor: { stepStrip: false, stepTableColumns: { showPt: false } } });
    const store = useSystemStore();
    store.loadSettings();

    expect(store.display.monitor.moduleFontScale).toEqual(DEFAULT_MODULE_FONT_SCALE);
    expect(store.display.monitor.stepStrip).toBe(false);
    expect(store.display.monitor.stepTableColumns.showPt).toBe(false);
    expect(store.display.monitor.stepTableColumns.showNo).toBe(true);
  });

  it('部分保存的分区字号深合并，缺失项恢复 100%', () => {
    savedSettings = JSON.stringify({ monitor: { moduleFontScale: { sop: 160, stepTable: 180 } } });
    const store = useSystemStore();
    store.loadSettings();

    expect(store.display.monitor.moduleFontScale).toEqual({ ...DEFAULT_MODULE_FONT_SCALE, sop: 160, stepTable: 180 });
    expect(store.display.monitor.moduleFontScale.charts).toBe(100);
    expect(store.display.monitor.moduleFontScale.controls).toBe(100);
  });
});
