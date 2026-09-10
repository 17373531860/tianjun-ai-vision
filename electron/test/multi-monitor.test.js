'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
  advanceCrashWindow,
  buildKioskHash,
  buildMainWindowHash,
  buildStationAssignments,
  displayTargetsOverlap,
  enumerateDisplaysForApply,
  filterStationAssignmentsByChannelCount,
  isMainRenderer,
  isWindowOnOccupiedTarget,
  normalizeMultiMonitorConfig,
  partitionResolvedStationAssignments,
  resolveDisplayTarget,
  toDisplayDto,
} = require('../multi-monitor');

const displays = [
  {
    id: 'primary',
    label: '主显示器',
    bounds: { x: 0, y: 0, width: 1920, height: 1080 },
    workArea: { x: 0, y: 0, width: 1920, height: 1040 },
  },
  {
    id: 'wavlink-1',
    label: 'WAVLINK',
    bounds: { x: 1920, y: 0, width: 1920, height: 1080 },
    workArea: { x: 1920, y: 0, width: 1920, height: 1040 },
  },
];

test('默认关闭且只读，非法映射会被过滤', () => {
  const { config, warnings } = normalizeMultiMonitorConfig({
    mapping: {
      '-1': { display_id: 'bad' },
      '0': { display_id: ' 100 ' },
      '1': { bounds: { x: 1920, y: 0, width: 1920, height: 1080 } },
      '2': { display_id: '', bounds: { x: 0, y: 0, width: 0.5, height: 1080 } },
      nope: { display_id: 'bad' },
    },
  });

  assert.equal(config.enabled, false);
  assert.equal(config.readonly, true);
  assert.deepEqual(config.mapping, {
    '0': { display_id: '100' },
    '1': { display_id: '', bounds: { x: 1920, y: 0, width: 1920, height: 1080 } },
  });
  assert.equal(warnings.length, 3);
});

test('旧 display_id 配置只生成可操作主屏映射', () => {
  const { config } = normalizeMultiMonitorConfig({
    enabled: true,
    mapping: { '2': { display_id: 'primary' } },
  });

  assert.deepEqual(buildStationAssignments(config.mapping), [{
    key: '2:main',
    channelId: 2,
    role: 'main',
    readonly: false,
    assignment: { display_id: 'primary' },
  }]);
});

test('旧副屏配置缺少开关时保留目标但默认关闭，视角默认 follow', () => {
  const { config, warnings } = normalizeMultiMonitorConfig({
    mapping: {
      '0': {
        display_id: ' primary ',
        aux_display_id: ' wavlink-1 ',
        aux_bounds: { x: 1920.8, y: 0, width: 1280.9, height: 720.4 },
      },
    },
  });

  assert.deepEqual(config.mapping['0'], {
    display_id: 'primary',
    aux_display_id: 'wavlink-1',
    aux_bounds: { x: 1920, y: 0, width: 1280, height: 720 },
    aux_hands_enabled: false,
    aux_view_mode: 'follow',
  });
  assert.equal(warnings.length, 0);
  const assignments = buildStationAssignments(config.mapping);
  assert.deepEqual(assignments.map((item) => item.role), ['main']);
});

test('只有严格启用手部副屏才生成 aux，缺失或非法模式默认 follow', () => {
  const { config } = normalizeMultiMonitorConfig({
    mapping: {
      '0': {
        display_id: 'primary',
        aux_display_id: 'wavlink-1',
        aux_hands_enabled: true,
      },
      '1': {
        display_id: 'main-1',
        aux_display_id: 'aux-1',
        aux_hands_enabled: true,
        aux_view_mode: 'unexpected',
      },
      '2': {
        display_id: 'main-2',
        aux_display_id: 'aux-2',
        aux_hands_enabled: 'true',
        aux_view_mode: 'fixed',
      },
    },
  });

  assert.equal(config.mapping['0'].aux_view_mode, 'follow');
  assert.equal(config.mapping['1'].aux_view_mode, 'follow');
  assert.equal(config.mapping['2'].aux_hands_enabled, false);
  const assignments = buildStationAssignments(config.mapping);
  assert.equal(assignments.find((item) => item.key === '0:aux').auxViewMode, 'follow');
  assert.equal(assignments.find((item) => item.key === '1:aux').auxViewMode, 'follow');
  assert.equal(assignments.some((item) => item.key === '2:aux'), false);
});

test('显式 fixed 保留，开关 true 但无副屏目标不会生成幽灵窗口', () => {
  const { config } = normalizeMultiMonitorConfig({
    mapping: {
      '0': {
        display_id: 'primary',
        aux_display_id: 'wavlink-1',
        aux_hands_enabled: true,
        aux_view_mode: 'fixed',
      },
      '1': {
        display_id: 'main-1',
        aux_hands_enabled: true,
      },
    },
  });

  assert.equal(config.mapping['0'].aux_view_mode, 'fixed');
  assert.equal(config.mapping['1'].aux_hands_enabled, undefined);
  const assignments = buildStationAssignments(config.mapping);
  assert.equal(assignments.find((item) => item.key === '0:aux').auxViewMode, 'fixed');
  assert.equal(assignments.some((item) => item.key === '1:aux'), false);
});

test('运行时按 channel_count 跳过历史工位但不改原映射', () => {
  const mapping = {
    '0': { display_id: 'primary', aux_display_id: 'aux-0', aux_hands_enabled: true },
    '2': { display_id: 'stale-main', aux_display_id: 'stale-aux', aux_hands_enabled: true },
  };
  const original = structuredClone(mapping);
  const result = filterStationAssignmentsByChannelCount(
    buildStationAssignments(mapping),
    2,
  );

  assert.deepEqual(result.assignments.map((item) => item.key), ['0:main', '0:aux']);
  assert.deepEqual(result.skippedChannelIds, [2]);
  assert.deepEqual(mapping, original);
});

test('优先按 display_id 定位并使用当前 bounds', () => {
  const target = resolveDisplayTarget({
    display_id: 'wavlink-1',
    bounds: { x: -9999, y: 0, width: 800, height: 600 },
  }, displays);

  assert.equal(target.source, 'display_id');
  assert.deepEqual(target.bounds, displays[1].bounds);
  assert.equal(target.warning, null);
});

test('显示器 ID 漂移时按记忆 bounds 匹配当前显示器', () => {
  const target = resolveDisplayTarget({
    display_id: 'old-wavlink-id',
    bounds: { x: 1920, y: 0, width: 1920, height: 1080 },
  }, displays);

  assert.equal(target.source, 'remembered_bounds');
  assert.equal(target.display.id, 'wavlink-1');
  assert.match(target.warning, /记忆坐标/);
});

test('枚举异常时保留手工 bounds 作为降级位置', () => {
  const displayResult = enumerateDisplaysForApply(() => {
    throw new Error('DisplayLink temporary failure');
  });
  const target = resolveDisplayTarget({
    display_id: 'missing',
    bounds: { x: 3840, y: 0, width: 1280, height: 720 },
  }, displayResult.displays);

  assert.deepEqual(displayResult.displays, []);
  assert.match(displayResult.warnings.join('\n'), /枚举失败.*bounds 降级/);
  assert.equal(target.source, 'manual_bounds');
  assert.deepEqual(target.bounds, { x: 3840, y: 0, width: 1280, height: 720 });
  assert.match(target.warning, /降级/);
});

test('主副屏 hash 分离且主屏不携带裁切参数', () => {
  assert.equal(
    buildKioskHash(3, 'main', 'follow'),
    '/monitor?channel=3&kiosk=1&readonly=0&multi_monitor=1',
  );
  assert.equal(
    buildKioskHash(3, 'aux'),
    '/monitor?channel=3&kiosk=1&readonly=1&multi_monitor=1&video_only=1&hands_crop=1&aux_view_mode=follow',
  );
  assert.equal(
    buildKioskHash(3, 'aux', 'follow'),
    '/monitor?channel=3&kiosk=1&readonly=1&multi_monitor=1&video_only=1&hands_crop=1&aux_view_mode=follow',
  );
  assert.equal(
    buildKioskHash(3, 'aux', 'fixed'),
    '/monitor?channel=3&kiosk=1&readonly=1&multi_monitor=1&video_only=1&hands_crop=1&aux_view_mode=fixed',
  );
  assert.equal(
    buildKioskHash(3, 'aux', 'invalid'),
    '/monitor?channel=3&kiosk=1&readonly=1&multi_monitor=1&video_only=1&hands_crop=1&aux_view_mode=follow',
  );
  assert.doesNotMatch(buildKioskHash(3, 'main', 'follow'), /hands_crop|video_only|aux_view_mode/);
});

test('复用主窗口的工位路由保留导航且锁定指定工位', () => {
  assert.equal(
    buildMainWindowHash(3),
    '/monitor?channel=3&station_view=1&readonly=0&multi_monitor=1',
  );
  assert.doesNotMatch(buildMainWindowHash(3), /kiosk=1|hands_crop|video_only/);
});

test('显示器 DTO 标识 OS 主屏与主窗口当前所在屏', () => {
  assert.deepEqual(toDisplayDto(displays[0], 'primary', 'primary'), {
    id: 'primary',
    label: '主显示器',
    bounds: { x: 0, y: 0, width: 1920, height: 1080 },
    workArea: { x: 0, y: 0, width: 1920, height: 1040 },
    isPrimary: true,
    isMainWindowDisplay: true,
  });
});

test('OS 主屏的工位 main 复用主应用窗，副屏仍创建且不误触发主窗避让', () => {
  const displayDtos = displays.map((display) => toDisplayDto(display, 'primary', 'primary'));
  const resolved = buildStationAssignments({
    '0': {
      display_id: 'primary',
      aux_display_id: 'wavlink-1',
      aux_hands_enabled: true,
    },
  }).map((item) => ({
    ...item,
    target: resolveDisplayTarget(item.assignment, displayDtos),
  }));
  const partition = partitionResolvedStationAssignments(resolved);

  assert.deepEqual(partition.reusedMainAssignments.map((item) => item.key), ['0:main']);
  assert.deepEqual(partition.stationWindowAssignments.map((item) => item.key), ['0:aux']);
  const stationTargets = partition.stationWindowAssignments.map((item) => item.target);
  assert.equal(isWindowOnOccupiedTarget(displays[0].bounds, stationTargets), false);
  assert.equal(isWindowOnOccupiedTarget(displays[1].bounds, stationTargets), true);
});

test('关闭手部副屏时副屏目标不创建窗口，也不参与主窗口避让', () => {
  const displayDtos = displays.map((display) => toDisplayDto(display, 'primary', 'primary'));
  const resolved = buildStationAssignments({
    '0': {
      display_id: 'primary',
      aux_display_id: 'wavlink-1',
      aux_hands_enabled: false,
    },
  }).map((item) => ({
    ...item,
    target: resolveDisplayTarget(item.assignment, displayDtos),
  }));
  const partition = partitionResolvedStationAssignments(resolved);

  assert.deepEqual(partition.reusedMainAssignments.map((item) => item.key), ['0:main']);
  assert.deepEqual(partition.stationWindowAssignments, []);
  assert.equal(isWindowOnOccupiedTarget(displays[1].bounds, []), false);
});

test('非 OS 主屏的 main 与 OS 主屏上的 aux 都不能复用主应用窗', () => {
  const displayDtos = displays.map((display) => toDisplayDto(display, 'primary', 'wavlink-1'));
  const resolved = [
    {
      key: '1:main',
      channelId: 1,
      role: 'main',
      target: resolveDisplayTarget({ display_id: 'wavlink-1' }, displayDtos),
    },
    {
      key: '0:aux',
      channelId: 0,
      role: 'aux',
      target: resolveDisplayTarget({ display_id: 'primary' }, displayDtos),
    },
  ];
  const partition = partitionResolvedStationAssignments(resolved);

  assert.deepEqual(partition.reusedMainAssignments, []);
  assert.deepEqual(
    partition.stationWindowAssignments.map((item) => item.key),
    ['1:main', '0:aux'],
  );
});

test('显示器枚举失败时，记忆 bounds 命中 OS 主屏仍复用主应用窗', () => {
  const primaryDisplay = toDisplayDto(displays[0], 'primary', 'wavlink-1');
  const resolved = [{
    key: '0:main',
    channelId: 0,
    role: 'main',
    target: resolveDisplayTarget({
      display_id: 'missing-primary-id',
      bounds: displays[0].bounds,
    }, []),
  }];

  const partition = partitionResolvedStationAssignments(resolved, primaryDisplay);

  assert.equal(resolved[0].target.source, 'manual_bounds');
  assert.deepEqual(partition.reusedMainAssignments.map((item) => item.key), ['0:main']);
  assert.deepEqual(partition.stationWindowAssignments, []);
});

test('主副屏及不同工位禁止使用重叠显示区域', () => {
  assert.equal(displayTargetsOverlap(
    { display: displays[0], bounds: displays[0].bounds },
    { display: displays[0], bounds: { x: 50, y: 50, width: 800, height: 600 } },
  ), true);
  assert.equal(displayTargetsOverlap(
    { display: null, bounds: { x: 0, y: 0, width: 1000, height: 800 } },
    { display: null, bounds: { x: 900, y: 100, width: 1000, height: 800 } },
  ), true);
  assert.equal(displayTargetsOverlap(
    { display: displays[0], bounds: displays[0].bounds },
    { display: displays[1], bounds: displays[1].bounds },
  ), false);
});

test('renderer crash 在 60 秒窗口内第 3 次停止恢复计数', () => {
  const first = advanceCrashWindow({}, 1000);
  const second = advanceCrashWindow(first, 30000);
  const third = advanceCrashWindow(second, 59000);

  assert.equal(first.reloadAttempts, 1);
  assert.equal(second.reloadAttempts, 2);
  assert.equal(third.reloadAttempts, 3);
});

test('renderer crash 超过 60 秒后重开计数窗口', () => {
  const previous = { reloadAttempts: 2, lastCrashAt: 1000 };

  assert.deepEqual(advanceCrashWindow(previous, 61002), {
    reloadAttempts: 1,
    lastCrashAt: 61002,
  });
});

test('只有主窗口 renderer sender 通过布局应用授权', () => {
  const mainWebContents = {};
  const kioskWebContents = {};

  assert.equal(isMainRenderer(mainWebContents, mainWebContents), true);
  assert.equal(isMainRenderer(kioskWebContents, mainWebContents), false);
  assert.equal(isMainRenderer(null, mainWebContents), false);
});
