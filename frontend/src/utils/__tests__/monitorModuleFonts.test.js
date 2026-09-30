import { describe, expect, it } from 'vitest';
import { DEFAULT_MODULE_FONT_SCALE, getModuleFontScale } from '../monitorModuleFonts';

describe('检测主页分区字号', () => {
  it('八个分区默认比例为 1，原字号不变', () => {
    expect(Object.keys(DEFAULT_MODULE_FONT_SCALE)).toHaveLength(8);
    for (const key of Object.keys(DEFAULT_MODULE_FONT_SCALE)) {
      expect(getModuleFontScale(DEFAULT_MODULE_FONT_SCALE, key)).toBe(1);
      expect(24 * getModuleFontScale(DEFAULT_MODULE_FONT_SCALE, key)).toBe(24);
    }
  });

  it('160% 只改变指定分区，图表字号使用相同比例', () => {
    const scales = { sop: 160, charts: 160 };
    expect(getModuleFontScale(scales, 'sop')).toBe(1.6);
    expect(getModuleFontScale(scales, 'stepTable')).toBe(1);
    expect(24 * getModuleFontScale(scales, 'charts')).toBeCloseTo(38.4);
  });

  it('旧设置缺项回退100%，无效值回退且越界档位受限', () => {
    expect(getModuleFontScale(undefined, 'sop')).toBe(1);
    expect(getModuleFontScale({ sop: 'bad' }, 'sop')).toBe(1);
    expect(getModuleFontScale({ sop: 60 }, 'sop')).toBe(0.8);
    expect(getModuleFontScale({ sop: 300 }, 'sop')).toBe(2);
  });
});
