import { describe, it, expect } from 'vitest';
import { collectOverlayDrawLabels } from '../overlayDrawLabels';

describe('collectOverlayDrawLabels', () => {
  it('停用的动作标签仍要画框（混合逐件配对数据源）', () => {
    const labels = collectOverlayDrawLabels([
      { label: '扫码', enabled: true },
      { label: '螺丝锁付位置', enabled: true, detect_role: 'item',
        per_item: { item_label: '螺丝锁付位置', action_label: '螺丝锁付-已完成' } },
      { label: '螺丝锁付-已完成', enabled: false, threshold: 40 },
      { label: '锁付完成', enabled: true },
    ]);
    expect(labels.has('螺丝锁付-已完成')).toBe(true);
    expect(labels.has('螺丝锁付位置')).toBe(true);
    expect(labels.has('扫码')).toBe(true);
  });

  it('数组 OR 形态同样放行', () => {
    const labels = collectOverlayDrawLabels([
      { label: '产品组', enabled: true,
        per_item: { item_label: ['产品1', '产品2'], action_label: ['打钉A'] } },
    ]);
    expect(labels.has('产品1')).toBe(true);
    expect(labels.has('产品2')).toBe(true);
    expect(labels.has('打钉A')).toBe(true);
  });

  it('普通顺序项目停用行仍不画框（零差异）', () => {
    const labels = collectOverlayDrawLabels([
      { label: '步骤A', enabled: true },
      { label: '步骤B', enabled: false },
    ]);
    expect(labels.has('步骤A')).toBe(true);
    expect(labels.has('步骤B')).toBe(false);
  });
});
