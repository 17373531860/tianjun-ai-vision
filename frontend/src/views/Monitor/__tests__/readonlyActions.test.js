import { describe, expect, it, vi } from 'vitest';
import { protectMonitorActions } from '../readonlyActions';

describe('protectMonitorActions', () => {
  it.each([
    'startDetectionForChannel',
    'stopDetectionForChannel',
    'standbyForChannel',
    'resetCountersForChannel',
    'setVideoProgressForChannel',
    'setVideoSpeedForChannel',
    'clearPendingScan',
    'toggleScanDisableFor',
  ])('参观窗调用 %s 不执行原写方法，仍可 await', async (name) => {
    const write = vi.fn(() => Promise.reject(new Error('不应发送写请求')));
    const actions = protectMonitorActions({ [name]: write }, () => true);

    await expect(actions[name](2, 0.5)).resolves.toBeUndefined();
    expect(write).not.toHaveBeenCalled();
  });

  it('参观窗保留格式化、查询、画框及帧尺寸同步的参数和返回值', () => {
    const canvas = {};
    const frameSizes = {};
    const drawing = { drawn: true };
    const source = {
      formatVideoTime: vi.fn((seconds) => `00:${seconds}`),
      getDisplayCT: vi.fn((channel) => channel.ct),
      renderDetectionOverlay: vi.fn(() => drawing),
      setFrameNaturalSize: vi.fn((channel, width, height) => {
        frameSizes[channel] = { width, height };
      }),
    };
    const actions = protectMonitorActions(source, () => true);

    for (const name of Object.keys(source)) expect(actions[name]).toBe(source[name]);
    expect(actions.formatVideoTime(12)).toBe('00:12');
    expect(actions.getDisplayCT({ ct: 3.5 })).toBe(3.5);
    expect(actions.renderDetectionOverlay(2, canvas)).toBe(drawing);
    expect(source.renderDetectionOverlay).toHaveBeenCalledWith(2, canvas);
    actions.setFrameNaturalSize(2, 1920, 1080);
    expect(frameSizes).toEqual({ 2: { width: 1920, height: 1080 } });
  });

  it('未来新增的未知 action 默认阻止，主窗仍能调用', async () => {
    let readonly = true;
    const updateCycleProgress = vi.fn(() => 'saved');
    const actions = protectMonitorActions({ updateCycleProgress }, () => readonly);

    await expect(actions.updateCycleProgress(1, 4)).resolves.toBeUndefined();
    expect(updateCycleProgress).not.toHaveBeenCalled();
    readonly = false;
    expect(actions.updateCycleProgress(1, 4)).toBe('saved');
    expect(updateCycleProgress).toHaveBeenCalledExactlyOnceWith(1, 4);
  });

  it('插件缓存的写方法在 可写 → 只读 → 可写 切换后仍按当前角色守门', async () => {
    let readonly = false;
    const result = Promise.resolve({ started: true });
    const start = vi.fn(() => result);
    const payload = { projectId: 8 };
    const actions = protectMonitorActions({ startDetectionForChannel: start }, () => readonly);
    const cachedStart = actions.startDetectionForChannel;

    expect(cachedStart(3, payload)).toBe(result);
    expect(start).toHaveBeenCalledExactlyOnceWith(3, payload);

    readonly = true;
    await expect(cachedStart(4, payload)).resolves.toBeUndefined();
    expect(start).toHaveBeenCalledTimes(1);

    readonly = false;
    expect(cachedStart(5, payload)).toBe(result);
    expect(start).toHaveBeenCalledTimes(2);
    expect(start).toHaveBeenLastCalledWith(5, payload);
  });

  it('主窗保留写方法抛出的错误', () => {
    const failure = new Error('写入失败');
    const stop = vi.fn(() => { throw failure; });
    const actions = protectMonitorActions({ stopDetectionForChannel: stop }, () => false);

    expect(() => actions.stopDetectionForChannel(0)).toThrow(failure);
  });
});
