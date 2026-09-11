// monitorModes 纯函数单测 — 巨石重构阶段1
// 语义基线 = v3.54.1 processChannelResult 内联版，这些用例是行为等价的守门。
import { describe, it, expect } from 'vitest';
import {
  regionEventRuleSteps,
  resolveLogicMode,
  resolveModePanelKind,
  resolveStepsToShow,
  projectLikeForChannelSteps,
  buildChannelStepViews,
} from '../monitorModes';

const chState = (over = {}) => ({
  currentCycleSteps: [],
  backupCoveredLabels: [],
  stepInflightDurations: {},
  prevSteps: [],
  prevTableData: [],
  resetPreviousResults: false,
  ...over,
});

describe('regionEventRuleSteps', () => {
  it('按规则名建卡并保留顺序', () => {
    const steps = regionEventRuleSteps({
      region_events: { rules: [
        { id: 7, name: '测硬度' },
        { id: 8, name: '扫码' },
        { name: '下工件' },
      ] },
    });
    expect(steps.map(s => s.label)).toEqual(['测硬度', '扫码', '下工件']);
    expect(steps[0]).toEqual({ id: 're_7', label: '测硬度', displayLabel: '测硬度', enabled: true });
    expect(steps[2].id).toBe('re_2'); // 无 id 回退下标
  });

  it('过滤无名规则，空/缺配置返回空数组', () => {
    expect(regionEventRuleSteps({ region_events: { rules: [{ id: 1 }, null] } })).toEqual([]);
    expect(regionEventRuleSteps({})).toEqual([]);
    expect(regionEventRuleSteps(undefined)).toEqual([]);
  });
});

describe('resolveLogicMode', () => {
  it('轮询配置优先，回退兜底项目', () => {
    expect(resolveLogicMode({ logic_mode: 'tracking' }, { logic_mode: 'sequential' })).toBe('tracking');
    expect(resolveLogicMode(null, { logic_mode: 'per_item' })).toBe('per_item');
    expect(resolveLogicMode(null, null)).toBeUndefined();
  });
});

describe('resolveModePanelKind', () => {
  it('tracking/per_item/weighing 换专属面板，步骤类走 SOP', () => {
    expect(resolveModePanelKind({ logic_mode: 'tracking' }, null)).toBe('tracking');
    expect(resolveModePanelKind({ logic_mode: 'per_item' }, null)).toBe('per_item');
    expect(resolveModePanelKind({ logic_mode: 'weighing' }, null)).toBe('weighing');
    expect(resolveModePanelKind({ logic_mode: 'region_events' }, null)).toBeNull();
    expect(resolveModePanelKind({ logic_mode: 'sequential' }, null)).toBeNull();
    expect(resolveModePanelKind({ logic_mode: 'detection' }, null)).toBeNull();
    expect(resolveModePanelKind({ logic_mode: 'custom_mix' }, null)).toBeNull();
  });

  it('轮询配置优先于兜底项目', () => {
    expect(resolveModePanelKind(
      { logic_mode: 'tracking' },
      { logic_mode: 'sequential' },
    )).toBe('tracking');
    expect(resolveModePanelKind(null, { logic_mode: 'weighing' })).toBe('weighing');
    expect(resolveModePanelKind(null, { logic_mode: 'sequential' })).toBeNull();
  });
});

describe('buildChannelStepViews — 守门与常规模式', () => {
  it('无 detections 且非 region_events 时不产出（保留旧值语义）', () => {
    const r = buildChannelStepViews({ project_config: { logic_mode: 'sequential' } }, null, chState());
    expect(r.tableData).toBeNull();
    expect(r.sopSteps).toBeNull();
    expect(r.hiddenLabels).toEqual(new Set());
  });

  it('detections 为空数组（truthy）也产出 — 与内联版行为一致', () => {
    const r = buildChannelStepViews({
      detections: [],
      project_config: {
        logic_mode: 'sequential',
        steps_config: [{ label: 'a', enabled: true }],
      },
    }, null, chState());
    expect(r.tableData).toHaveLength(1);
    expect(r.tableData[0]).toMatchObject({ label: 'a', status: 'pending', cycleResult: null });
  });

  it('进过 cycle_steps 或备份覆盖即 completed；disabled/is_backup 被过滤', () => {
    const r = buildChannelStepViews({
      detections: [],
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
          { label: 'c', enabled: false },
          { label: 'd', enabled: true, is_backup: true },
        ],
      },
    }, null, chState({ currentCycleSteps: ['a'], backupCoveredLabels: ['b'] }));
    expect(r.tableData.map(x => [x.label, x.status])).toEqual([
      ['a', 'completed'], ['b', 'completed'],
    ]);
  });

  it('载荷缺 steps_config 时回退兜底项目', () => {
    const r = buildChannelStepViews(
      { detections: [], project_config: { logic_mode: 'sequential' } },
      { steps_config: [{ label: 'fb', enabled: true }] },
      chState(),
    );
    expect(r.tableData.map(x => x.label)).toEqual(['fb']);
  });

  it('普通顺序模式与主屏一致渲染逐步骤 OK/NG', () => {
    const payload = {
      detections: [],
      cycle_sum_step_durations: { a: 0.8, b: 1.1 },
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
        ],
      },
    };
    const r = buildChannelStepViews(
      payload,
      null,
      chState({ currentCycleSteps: ['b', 'a'] }),
    );

    expect(r.tableData.map(x => [x.label, x.status, x.cycleResult])).toEqual([
      ['a', 'completed', 'ok'],
      ['b', 'completed', 'ng'],
    ]);
  });

  it('无序 detection 的中间步骤交换顺序仍全部为 OK', () => {
    const r = buildChannelStepViews({
      detections: [],
      cycle_sum_step_durations: { a: 0.5, b: 0.6, c: 0.7, d: 0.8 },
      project_config: {
        logic_mode: 'detection',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
          { label: 'c', enabled: true },
          { label: 'd', enabled: true },
        ],
      },
    }, null, chState({ currentCycleSteps: ['a', 'c', 'b', 'd'] }));

    expect(r.tableData.map(x => [x.label, x.status, x.cycleResult])).toEqual([
      ['a', 'completed', 'ok'],
      ['b', 'completed', 'ok'],
      ['c', 'completed', 'ok'],
      ['d', 'completed', 'ok'],
    ]);
  });

  it('周期间隙保留上一轮逐步骤结果，新周期首步未入列时也清除旧结果', () => {
    const project_config = {
      logic_mode: 'sequential',
      steps_config: [
        { label: 'a', enabled: true },
        { label: 'b', enabled: true },
      ],
    };
    const settled = buildChannelStepViews(
      {
        detections: [],
        cycle_sum_step_durations: { a: 0.8, b: 1.1 },
        project_config,
      },
      null,
      chState({ currentCycleSteps: ['b', 'a'] }),
    );
    const idle = buildChannelStepViews(
      { detections: [], project_config },
      null,
      chState({ prevTableData: settled.tableData }),
    );
    const nextCycle = buildChannelStepViews(
      {
        detections: [],
        cycle_sum_step_durations: {},
        project_config,
      },
      null,
      chState({
        currentCycleSteps: [],
        prevTableData: idle.tableData,
        resetPreviousResults: true,
      }),
    );

    expect(idle.tableData.map(x => x.cycleResult)).toEqual(['ok', 'ng']);
    expect(nextCycle.tableData.map(x => x.cycleResult)).toEqual([null, null]);
    expect(nextCycle.tableData.map(x => x.status)).toEqual(['pending', 'pending']);
    expect(settled.sopSteps.map(x => x.cycleResult)).toEqual(['ok', 'ng']);
  });

  it('新周期边界后的连续空轮询不会让上一轮新鲜事件重新回灌', () => {
    const project_config = {
      logic_mode: 'sequential',
      steps_config: [
        { label: 'a', enabled: true },
        { label: 'b', enabled: true },
      ],
    };
    const staleEventPayload = {
      detections: [],
      project_config,
      recent_events: [{ event_id: '1', seq: 9, timestamp: 300, reason: '上一轮顺序正确' }],
    };
    const boundaryMs = 300_100;
    const firstPoll = buildChannelStepViews(staleEventPayload, null, chState({
      resetPreviousResults: true,
      resultEventIgnoreBeforeMs: boundaryMs,
      cycleInProgress: false,
      nowMs: 300_100,
    }));
    const secondPoll = buildChannelStepViews(staleEventPayload, null, chState({
      prevTableData: firstPoll.tableData,
      resultEventIgnoreBeforeMs: boundaryMs,
      cycleInProgress: false,
      nowMs: 300_200,
    }));

    expect(firstPoll.tableData.map(x => x.cycleResult)).toEqual([null, null]);
    expect(secondPoll.tableData.map(x => x.cycleResult)).toEqual([null, null]);
  });

  it('结算清空步骤后用新鲜 OK 结算提示补齐末步，并同步表格与 SOP', () => {
    const project_config = {
      logic_mode: 'sequential',
      steps_config: [
        { label: 'a', enabled: true },
        { label: 'b', enabled: true },
      ],
    };
    const beforeSettle = buildChannelStepViews({
      detections: [{ label: 'b' }],
      cycle_sum_step_durations: { a: 0.8 },
      project_config,
    }, null, chState({ currentCycleSteps: ['a', 'b'] }));
    const settled = buildChannelStepViews({
      detections: [],
      cycle_sum_step_durations: { a: 0.8, b: 1.1 },
      recent_events: [{ event_id: '1', seq: 7, timestamp: 100, reason: '顺序正确' }],
      project_config,
    }, null, chState({ prevTableData: beforeSettle.tableData, nowMs: 100_100 }));

    expect(beforeSettle.tableData.map(x => x.cycleResult)).toEqual(['ok', null]);
    expect(settled.tableData.map(x => [x.status, x.cycleResult, x.resultFinalized])).toEqual([
      ['completed', 'ok', true],
      ['completed', 'ok', true],
    ]);
    expect(settled.sopSteps.map(x => x.cycleResult)).toEqual(['ok', 'ok']);
  });

  it('NG 结算提示把明确缺失步骤标红，已做步骤保持 OK', () => {
    const r = buildChannelStepViews({
      detections: [],
      cycle_sum_step_durations: { a: 0.8 },
      recent_events: [{
        event_id: 2,
        seq: 8,
        timestamp: 200,
        reason: "周期不完整，缺少: ['b']",
      }],
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
        ],
      },
    }, null, chState({ nowMs: 200_100 }));

    expect(r.tableData.map(x => [x.status, x.cycleResult, x.resultFinalized])).toEqual([
      ['completed', 'ok', true],
      ['pending', 'ng', true],
    ]);
    expect(r.sopSteps.map(x => x.cycleResult)).toEqual(['ok', 'ng']);
  });

  it('明确缺项清单允许 A-B-A 的非缺项重复步骤保持 OK', () => {
    const r = buildChannelStepViews({
      detections: [],
      cycle_sum_step_durations: { a: 0.8 },
      recent_events: [{
        event_id: 2,
        seq: 80,
        timestamp: 204,
        reason: "周期不完整，缺少: ['b']",
      }],
      project_config: {
        logic_mode: 'sequential',
        pipeline_config: {
          sequence_order: [{ step_id: 1 }, { step_id: 2 }, { step_id: 1 }],
        },
        steps_config: [
          { id: 1, label: 'a', enabled: true },
          { id: 2, label: 'b', enabled: true },
        ],
      },
    }, null, chState({ nowMs: 204_100 }));

    expect(r.tableData.map(x => x.cycleResult)).toEqual(['ok', 'ng', 'ok']);
  });

  it('缺步 NG 事件只消费一次，后续轮询靠 finalized 前态持续保留 pending+NG', () => {
    const event = {
      event_id: 2,
      seq: 81,
      timestamp: 205,
      reason: "周期不完整，缺少: ['b']",
    };
    const payload = {
      detections: [],
      cycle_sum_step_durations: { a: 0.8 },
      recent_events: [event],
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
        ],
      },
    };
    const first = buildChannelStepViews(payload, null, chState({ nowMs: 205_100 }));
    const second = buildChannelStepViews(payload, null, chState({
      prevTableData: first.tableData,
      lastHandledResultEventKey: first.handledResultEventKey,
      nowMs: 205_250,
    }));

    expect(first.handledResultEventKey).toBe('2|81|205');
    expect(second.handledResultEventKey).toBeNull();
    expect(second.tableData.map(x => [x.status, x.cycleResult, x.resultFinalized])).toEqual([
      ['completed', 'ok', true],
      ['pending', 'ng', true],
    ]);
  });

  it('外部事件响应和补做挂起事件不能冒充周期结算结果', () => {
    const project_config = {
      logic_mode: 'sequential',
      steps_config: [{ label: 'a', enabled: true }],
    };
    const external = buildChannelStepViews({
      detections: [],
      project_config,
      recent_events: [{ event_id: 1, seq: 82, timestamp: 210, source: 'channel_group' }],
    }, null, chState({ nowMs: 210_100 }));
    const remediation = buildChannelStepViews({
      detections: [],
      project_config,
      pending_remediation: { kind: 'missing_step' },
      recent_events: [{ event_id: 2, seq: 83, timestamp: 211, remediation: true }],
    }, null, chState({ nowMs: 211_100 }));

    expect(external.tableData[0].cycleResult).toBeNull();
    expect(remediation.tableData[0].cycleResult).toBeNull();
  });

  it('周期计数器确认的最终 verdict 优先于可能被插件翻转前写入的事件 ID', () => {
    const r = buildChannelStepViews({
      detections: [],
      recent_events: [{ event_id: 2, seq: 84, timestamp: 215, reason: "缺少: ['b']" }],
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
        ],
      },
    }, null, chState({ settledVerdict: 'ok', nowMs: 215_100 }));

    expect(r.tableData.map(x => x.cycleResult)).toEqual(['ok', 'ok']);
  });

  it('NG 结算提示的“第 N 步顺序错误”精确标记对应位置，不能全步 OK', () => {
    const r = buildChannelStepViews({
      detections: [],
      cycle_sum_step_durations: { a: 0.8, b: 1.1 },
      recent_events: [{ event_id: 2, seq: 10, timestamp: 220, reason: '第2步顺序错误' }],
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
        ],
      },
    }, null, chState({ nowMs: 220_100 }));

    expect(r.tableData.map(x => x.cycleResult)).toEqual(['ok', 'ng']);
  });

  it('无法定位具体步骤的 NG 不猜测位置，也不能用 PT 伪造全步 OK', () => {
    const r = buildChannelStepViews({
      detections: [],
      cycle_sum_step_durations: { a: 0.8, b: 1.1 },
      recent_events: [{ event_id: 2, seq: 11, timestamp: 230, reason: '物品校验未通过' }],
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
        ],
      },
    }, null, chState({ nowMs: 230_100 }));

    expect(r.tableData.map(x => x.cycleResult)).toEqual([null, null]);
  });

  it('custom 条件事件不能把未参与条件的全部步骤盖成 OK', () => {
    const r = buildChannelStepViews({
      detections: [],
      recent_events: [{
        event_id: 1,
        seq: 111,
        timestamp: 235,
        reason: "自定义条件匹配: ['a']",
      }],
      project_config: {
        logic_mode: 'custom',
        custom_based_on: 'sequential',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
        ],
      },
    }, null, chState({ nowMs: 235_100 }));

    expect(r.tableData.map(x => x.cycleResult)).toEqual([null, null]);
  });

  it('重复标签 NG 依据前态唯一未决位置落点，不把 A-B-A 两张 A 全部染红', () => {
    const r = buildChannelStepViews({
      detections: [],
      cycle_sum_step_durations: { a: 0.8, b: 0.6 },
      recent_events: [{ event_id: 2, seq: 12, timestamp: 240, reason: "重复步骤: ['a']" }],
      project_config: {
        logic_mode: 'sequential',
        pipeline_config: {
          sequence_order: [{ step_id: 1 }, { step_id: 2 }, { step_id: 1 }],
        },
        steps_config: [
          { id: 1, label: 'a', enabled: true },
          { id: 2, label: 'b', enabled: true },
        ],
      },
    }, null, chState({
      prevTableData: [
        { label: 'a', status: 'completed', cycleResult: 'ok' },
        { label: 'b', status: 'completed', cycleResult: 'ok' },
        { label: 'a', status: 'pending', cycleResult: null },
      ],
      nowMs: 240_100,
    }));

    expect(r.tableData.map(x => x.cycleResult)).toEqual(['ok', 'ok', 'ng']);
  });

  it('A-B-A 重复标签按位置分配，不会把第一张 A 状态串给第三张', () => {
    const project_config = {
      logic_mode: 'sequential',
      pipeline_config: {
        sequence_order: [{ step_id: 1 }, { step_id: 2 }, { step_id: 1 }],
      },
      steps_config: [
        { id: 1, label: 'a', enabled: true },
        { id: 2, label: 'b', enabled: true },
      ],
    };
    const partial = buildChannelStepViews({
      detections: [],
      cycle_sum_step_durations: { a: 0.4 },
      project_config,
    }, null, chState({ currentCycleSteps: ['a'] }));
    const complete = buildChannelStepViews({
      detections: [],
      cycle_sum_step_durations: { a: 0.8, b: 0.6 },
      project_config,
    }, null, chState({ currentCycleSteps: ['a', 'b', 'a'] }));

    expect(partial.tableData.map(x => [x.label, x.status, x.cycleResult])).toEqual([
      ['a', 'completed', 'ok'],
      ['b', 'pending', null],
      ['a', 'pending', null],
    ]);
    expect(complete.tableData.map(x => x.cycleResult)).toEqual(['ok', 'ok', 'ok']);
  });
});

describe('buildChannelStepViews — region_events', () => {
  const payload = {
    project_config: {
      logic_mode: 'region_events',
      pipeline_config: { region_events: { rules: [
        { id: 1, name: '测硬度' }, { id: 2, name: '扫码' },
      ] } },
    },
  };

  it('无 detections 也建卡（区域事件模式专属守门）', () => {
    const r = buildChannelStepViews(payload, null, chState());
    expect(r.tableData.map(x => x.step)).toEqual(['测硬度', '扫码']);
    expect(r.sopSteps.map(x => x.name)).toEqual(['测硬度', '扫码']);
  });

  it('in-flight 时长 > 0 的规则点亮为 active', () => {
    const r = buildChannelStepViews(payload, null,
      chState({ stepInflightDurations: { '扫码': 1.2 } }));
    expect(r.tableData.find(x => x.label === '扫码').status).toBe('active');
    expect(r.tableData.find(x => x.label === '测硬度').status).toBe('pending');
  });

  it('载荷无 rules 时回退兜底项目的 pipeline_config', () => {
    const r = buildChannelStepViews(
      { project_config: { logic_mode: 'region_events' } },
      { pipeline_config: { region_events: { rules: [{ id: 9, name: '兜底规则' }] } } },
      chState(),
    );
    expect(r.tableData.map(x => x.label)).toEqual(['兜底规则']);
  });

  it('区域事件模式不把通用 recent_events 文案推断成步骤 NG', () => {
    const r = buildChannelStepViews({
      ...payload,
      recent_events: [{ event_id: 2, timestamp: 300, reason: "缺少: ['扫码']" }],
    }, null, chState({ nowMs: 300_100 }));
    expect(r.tableData.map(x => x.cycleResult)).toEqual([null, null]);
  });
});

describe('buildChannelStepViews — tracking', () => {
  it('item_checklist counted>0 → completed + cycleResult ok', () => {
    const r = buildChannelStepViews({
      detections: [],
      tracking: { item_checklist: { '螺丝': { counted: 2 }, '垫片': { counted: 0 } } },
      project_config: {
        logic_mode: 'tracking',
        steps_config: [
          { label: '螺丝', enabled: true },
          { label: '垫片', enabled: true },
        ],
      },
    }, null, chState());
    expect(r.tableData.find(x => x.label === '螺丝')).toMatchObject({ status: 'completed', cycleResult: 'ok' });
    expect(r.tableData.find(x => x.label === '垫片')).toMatchObject({ status: 'pending', cycleResult: null });
  });

  it('期望清单过滤：只展示 counting_expected_list 中的项目', () => {
    const r = buildChannelStepViews({
      detections: [],
      tracking: {},
      project_config: {
        logic_mode: 'tracking',
        pipeline_config: { counting_expected_list: [{ label: '螺丝' }] },
        steps_config: [
          { label: '螺丝', enabled: true },
          { label: '箱子', enabled: true },
        ],
      },
    }, null, chState());
    expect(r.tableData.map(x => x.label)).toEqual(['螺丝']);
  });

  it('未填清单时仅排除容器 label', () => {
    const r = buildChannelStepViews({
      detections: [],
      tracking: {},
      project_config: {
        logic_mode: 'tracking',
        pipeline_config: { tracking_container_label: '箱子' },
        steps_config: [
          { label: '螺丝', enabled: true },
          { label: '箱子', enabled: true },
        ],
      },
    }, null, chState());
    expect(r.tableData.map(x => x.label)).toEqual(['螺丝']);
  });

  it('容器模式：任意箱子里 counted>0 即命中', () => {
    const r = buildChannelStepViews({
      detections: [],
      tracking: {
        container_mode: true,
        boxes: { b1: {}, b2: {} },
        item_checklist: { _boxes: { b2: { items: { '螺丝': { counted: 1 } } } } },
      },
      project_config: {
        logic_mode: 'tracking',
        steps_config: [{ label: '螺丝', enabled: true }],
      },
    }, null, chState());
    expect(r.tableData[0]).toMatchObject({ label: '螺丝', status: 'completed', cycleResult: 'ok' });
  });

  it('tracking 新周期 checklist 清空时不继承上一轮 OK', () => {
    const r = buildChannelStepViews({
      detections: [],
      tracking: { item_checklist: { '螺丝': { counted: 0 } } },
      project_config: {
        logic_mode: 'tracking',
        steps_config: [{ label: '螺丝', enabled: true }],
      },
    }, null, chState({
      resetPreviousResults: true,
      prevTableData: [{ label: '螺丝', status: 'completed', cycleResult: 'ok' }],
    }));
    expect(r.tableData[0]).toMatchObject({ status: 'pending', cycleResult: null });
  });
});

describe('buildChannelStepViews — SOP 缩略图与隐藏标签', () => {
  it('后端有新图用新图，没有按 label 从上一轮继承（图永不空）', () => {
    const r = buildChannelStepViews({
      detections: [],
      step_screenshots: { 'a': 'NEWB64' },
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { label: 'a', enabled: true },
          { label: 'b', enabled: true },
        ],
      },
    }, null, chState({ prevSteps: [
      { label: 'a', screenshot: 'data:image/jpeg;base64,OLD_A' },
      { label: 'b', screenshot: 'data:image/jpeg;base64,OLD_B' },
    ] }));
    expect(r.sopSteps.find(s => s.label === 'a').screenshot).toBe('data:image/jpeg;base64,NEWB64');
    expect(r.sopSteps.find(s => s.label === 'b').screenshot).toBe('data:image/jpeg;base64,OLD_B');
  });

  it('hide_in_view 标签进 hiddenLabels 集合（每轮都产出）', () => {
    const r = buildChannelStepViews({
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { label: 'a', hide_in_view: true },
          { label: 'b' },
        ],
      },
    }, null, chState());
    expect(r.hiddenLabels).toEqual(new Set(['a']));
  });
});

const trapSteps = [
  { id: 1, label: '检查外观', enabled: true },
  { id: 2, label: '未进序列', enabled: true },
];

describe('resolveStepsToShow', () => {
  it('sequential 按 sequence 排序，重复 label 保留，未进序列的不出现', () => {
    const steps = resolveStepsToShow({
      logic_mode: 'sequential',
      steps_config: trapSteps,
      pipeline_config: { sequence_order: [{ step_id: 1 }, { step_id: 1 }] },
    });
    expect(steps.map(s => s.label)).toEqual(['检查外观', '检查外观']);
  });

  it('empty sequence 仍摊 enabled 步骤（旧 mock / 缺键语义）', () => {
    const steps = resolveStepsToShow({
      logic_mode: 'sequential',
      steps_config: [
        { id: 1, label: 'a', enabled: true },
        { id: 2, label: 'b', enabled: false },
      ],
      pipeline_config: {},
    });
    expect(steps.map(s => s.label)).toEqual(['a']);
  });

  it('detection 按 detection_steps 映射', () => {
    const steps = resolveStepsToShow({
      logic_mode: 'detection',
      steps_config: trapSteps,
      pipeline_config: { detection_steps: [1, 1] },
    });
    expect(steps.map(s => s.label)).toEqual(['检查外观', '检查外观']);
  });

  it('custom sequential 走 custom_sequence_order', () => {
    const steps = resolveStepsToShow({
      logic_mode: 'custom',
      custom_based_on: 'sequential',
      steps_config: trapSteps,
      pipeline_config: { custom_sequence_order: [{ step_id: 1 }] },
    });
    expect(steps.map(s => s.label)).toEqual(['检查外观']);
  });

  it('custom 条件模式只展示 sequence 里出现的步骤', () => {
    const steps = resolveStepsToShow({
      logic_mode: 'custom',
      custom_based_on: 'none',
      steps_config: trapSteps,
      pipeline_config: { custom_conditions: [{ sequence: [1] }] },
    });
    expect(steps.map(s => s.label)).toEqual(['检查外观']);
  });

  it('滤掉 backup_for', () => {
    const steps = resolveStepsToShow({
      logic_mode: 'sequential',
      steps_config: [
        { id: 1, label: '主', enabled: true },
        { id: 2, label: '替', enabled: true, backup_for: 1 },
      ],
    });
    expect(steps.map(s => s.label)).toEqual(['主']);
  });
});

describe('projectLikeForChannelSteps — 禁止 fallback sequence 串项目', () => {
  it('poll 已有 project_config 时不用兜底项目的 sequence', () => {
    const like = projectLikeForChannelSteps(
      {
        logic_mode: 'sequential',
        steps_config: trapSteps,
        pipeline_config: { sequence_order: [] },
      },
      {
        logic_mode: 'sequential',
        steps_config: [{ id: 9, label: 'navbar-only', enabled: true }],
        pipeline_config: { sequence_order: [{ step_id: 9 }] },
      },
    );
    expect(like.sequence_order).toEqual([]);
    expect(resolveStepsToShow(like).map(s => s.label)).toEqual(['检查外观', '未进序列']);
  });

  it('poll 缺 steps_config 时可用兜底步骤，但仍用 poll 的 sequence', () => {
    const like = projectLikeForChannelSteps(
      {
        logic_mode: 'sequential',
        pipeline_config: { sequence_order: [{ step_id: 1 }] },
      },
      { steps_config: trapSteps },
    );
    expect(resolveStepsToShow(like).map(s => s.label)).toEqual(['检查外观']);
  });
});

describe('buildChannelStepViews — sequence 建卡', () => {
  it('按 sequence 建卡：排序、重复 label 两张卡、陷阱步骤不出现', () => {
    const r = buildChannelStepViews({
      detections: [],
      project_config: {
        logic_mode: 'sequential',
        steps_config: trapSteps,
        pipeline_config: { sequence_order: [{ step_id: 1 }, { step_id: 1 }] },
      },
    }, {
      steps_config: [{ id: 9, label: 'navbar-only', enabled: true }],
      pipeline_config: { sequence_order: [{ step_id: 9 }] },
    }, chState());
    expect(r.tableData.map(x => x.label)).toEqual(['检查外观', '检查外观']);
    expect(r.sopSteps.map(x => x.name)).toEqual(['检查外观', '检查外观']);
  });

  it('poll 空 sequence 不吃兜底项目的 sequence', () => {
    const r = buildChannelStepViews({
      detections: [],
      project_config: {
        logic_mode: 'sequential',
        steps_config: [
          { id: 1, label: 'poll-a', enabled: true },
          { id: 2, label: 'poll-b', enabled: true },
        ],
        pipeline_config: {},
      },
    }, {
      steps_config: [{ id: 9, label: 'navbar-only', enabled: true }],
      pipeline_config: { sequence_order: [{ step_id: 9 }] },
    }, chState());
    expect(r.tableData.map(x => x.label)).toEqual(['poll-a', 'poll-b']);
  });
});
