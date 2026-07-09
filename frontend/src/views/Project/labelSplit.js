// ==================== 同标签区域拆分（虚拟步骤）前端助手 (v3.32) ====================
// 配置结构见 docs/rfc/同标签区域拆分_虚拟步骤_设计方案_RFC.md。
// 职责: 规则默认值 / 保存前校验 / 「区域名 ⇄ steps_config 虚拟步骤」双向同步。
// 虚拟步骤 = steps_config 里带 split_origin=<rule.id> 标记的普通步骤行,
// 下游状态机 / 顺序 / 事件 / MES 对它零特殊处理。
import { applyStepEnabledChange } from './stepEnabled';

export const createDefaultSplitRule = () => ({
  id: `ls_${Date.now()}_${Math.floor(Math.random() * 1000)}`,
  enabled: true,
  source_label: '',
  mode: 'fixed',                 // fixed=固定画面 | anchor=锚点跟随
  anchor_label: '',
  anchor_ref: null,              // anchor 模式: 标定时锚点框 {x,y,w,h} 归一化
  anchor_hold_seconds: 3.0,
  unmatched: 'drop',             // drop=丢弃 | keep=保留原标签 | map=改写
  unmatched_label: '',
  regions: [],                   // [{name, polygon:[[nx,ny],...], color}]
  // 多轮次 (v3.32): 同一批区域按工序轮次映射不同虚拟步骤
  // （前罩/后罩各打4颗: 检测到「盖罩」重新出现即切下一轮, 步骤名 = 前缀+区域名）
  rounds: {
    enabled: false,
    trigger_label: '',           // 轮次切换标签(如 盖罩): 每次重新出现 → 下一轮
    count: 2,
    prefixes: ['前罩', '后罩'],   // 每轮前缀, 数量 = count
    trigger_gap_seconds: 3.0,    // 切换标签消失多久后再出现才算"新一轮"
    trigger_min_seconds: 0.5,    // 切换标签需持续在场多久才确认切换(过滤单帧误检; 0=见帧即切)
    trigger_conf: 0,             // 切换标签专用置信度下限(挡低置信预备动作; 0=不额外过滤)
    region_overrides: {},        // 每轮独立区域(翻面后位置不重叠时用): {"2": [{name,polygon,color}]}
  },                             // 缺省轮沿用共享 regions
});

const _regionNames = (regions) => (regions || [])
  .map(r => String(r?.name || '').trim())
  .filter(n => n);

/** 某一轮(1起)生效的区域列表: 该轮配了独立区域用独立的, 否则用共享区域。 */
export function roundRegions(rule, roundIdx) {
  const overrides = rule?.rounds?.region_overrides;
  const ov = overrides && overrides[String(roundIdx)];
  if (Array.isArray(ov) && ov.some(r => r && String(r.name || '').trim())) return ov;
  return rule.regions || [];
}

/** 规则的虚拟步骤名全集（含轮次展开 + 每轮独立区域）。region 名合法性由调用方保证。 */
export function splitRuleStepNames(rule) {
  const names = [];
  const rounds = rule.rounds;
  if (rounds && rounds.enabled && Array.isArray(rounds.prefixes) && rounds.prefixes.length >= 2) {
    rounds.prefixes.forEach((prefix, i) => {
      for (const n of _regionNames(roundRegions(rule, i + 1))) {
        names.push(`${String(prefix).trim()}${n}`);
      }
    });
  } else {
    names.push(..._regionNames(rule.regions));
  }
  return names;
}

// 四象限模板: 一键生成 4 个区域（对角打螺丝最常见布局, 生成后可逐个重画）
export const QUADRANT_TEMPLATE = [
  { name: '螺丝1', polygon: [[0.05, 0.05], [0.5, 0.05], [0.5, 0.5], [0.05, 0.5]], color: '#f97316' },
  { name: '螺丝2', polygon: [[0.5, 0.05], [0.95, 0.05], [0.95, 0.5], [0.5, 0.5]], color: '#22d3ee' },
  { name: '螺丝3', polygon: [[0.05, 0.5], [0.5, 0.5], [0.5, 0.95], [0.05, 0.95]], color: '#a78bfa' },
  { name: '螺丝4', polygon: [[0.5, 0.5], [0.95, 0.5], [0.95, 0.95], [0.5, 0.95]], color: '#84cc16' },
];

const _validPolygon = (poly) => Array.isArray(poly) && poly.length >= 3;

/**
 * 保存前整体校验。返回错误文案 (string) 或 null。
 * 校验项: source_label 必填且不重复 / anchor 模式要素齐 / 区域 ≥1 且名字合法 /
 * 区域名不得与「非本规则来源」的既有步骤 label 或模型原始标签冲突 / 跨规则不重名。
 */
export function validateSplitRules(project) {
  const rules = (project?.pipeline_config?.label_splits || []).filter(r => r && r.enabled !== false);
  if (rules.length === 0) return null;
  const seenSources = new Set();
  const seenRegionNames = new Map();  // name -> rule.id
  const modelLabels = new Set(project.model_labels || []);
  for (const rule of rules) {
    const src = String(rule.source_label || '').trim();
    if (!src) return '拆分规则缺少「原始标签」';
    if (seenSources.has(src)) return `原始标签「${src}」配置了多条启用的拆分规则, 请停用其一`;
    seenSources.add(src);
    if (rule.mode === 'anchor') {
      if (!String(rule.anchor_label || '').trim()) return `规则「${src}」为锚点跟随模式, 但未选择锚点标签`;
      const ref = rule.anchor_ref;
      if (!ref || !(ref.w > 0) || !(ref.h > 0)) return `规则「${src}」为锚点跟随模式, 但未标定锚点参考框（在编辑器里点「抓取锚点框」）`;
    }
    const regions = (rule.regions || []).filter(r => r && String(r.name || '').trim() && _validPolygon(r.polygon));
    if (regions.length === 0) return `规则「${src}」没有任何画好的区域`;
    // 区域名与原始标签同名 → 只在未开多轮次时才是真冲突（虚拟步骤名=区域名,
    // 会和模型标签自我映射）; 开了多轮次后最终名带轮次前缀（如"前罩力矩"）,
    // 区域名沿用原始标签反而是"力矩/标记只按轮次拆"场景的自然写法, 不拦
    const roundsOn = !!(rule.rounds && rule.rounds.enabled);
    if (!roundsOn) {
      for (const region of regions) {
        if (String(region.name).trim() === src) return `规则「${src}」的区域名不能与原始标签同名`;
      }
    }
    // 多轮次配置校验
    const rounds = rule.rounds;
    if (rounds && rounds.enabled) {
      if (!String(rounds.trigger_label || '').trim()) return `规则「${src}」启用了多轮次, 但未选择轮次切换标签`;
      const cnt = Number(rounds.count) || 0;
      if (cnt < 2 || cnt > 8) return `规则「${src}」的轮数需在 2~8 之间`;
      const prefixes = (rounds.prefixes || []).map(p => String(p || '').trim());
      if (prefixes.length !== cnt || prefixes.some(p => !p)) return `规则「${src}」每一轮都要填前缀（共 ${cnt} 轮）`;
      if (new Set(prefixes).size !== cnt) return `规则「${src}」的轮次前缀不能重复`;
      // 每轮独立区域: 配了就要画完整、名字不重
      for (const [key, list] of Object.entries(rounds.region_overrides || {})) {
        const rnd = Number(key);
        if (!(rnd >= 1 && rnd <= cnt) || !Array.isArray(list) || list.length === 0) continue;
        const valid = list.filter(r => r && String(r.name || '').trim() && _validPolygon(r.polygon));
        if (valid.length === 0) return `规则「${src}」第 ${rnd} 轮的独立区域没有一个画完整`;
        const names = valid.map(r => String(r.name).trim());
        if (new Set(names).size !== names.length) return `规则「${src}」第 ${rnd} 轮的独立区域名重复`;
      }
    }
    // 最终虚拟步骤名（含轮次展开）冲突检查
    for (const name of splitRuleStepNames(rule)) {
      if (modelLabels.has(name)) return `规则「${src}」的虚拟步骤名「${name}」与模型标签重名, 请改名`;
      const owner = seenRegionNames.get(name);
      if (owner && owner !== rule.id) return `虚拟步骤名「${name}」在多条拆分规则中重复, 请改名`;
      seenRegionNames.set(name, rule.id);
      const conflict = (project.steps_config || []).find(
        s => s && s.label === name && s.split_origin !== rule.id
      );
      if (conflict) return `虚拟步骤名「${name}」与既有步骤重名（非本规则生成）, 请改名`;
    }
  }
  return null;
}

/**
 * 「拆分规则 → 虚拟步骤」同步（保存前调用, 幂等）:
 *  1. 每个启用规则的每个区域名 → steps_config 里保证有一行 split_origin=rule.id 的步骤
 *     （新建的自动进 sequence/detection 清单, 与手工启用步骤同一套 applyStepEnabledChange）
 *  2. unmatched=map 的改写标签 → 也生成虚拟步骤但默认禁用（“位置外操作”要不要参与判定由用户显式开）
 *  3. 孤儿清理: split_origin 指向的规则已删 / 区域已改名 → 步骤连同序列引用一并移除
 * 返回 {added: [label...], removed: [label...]}。
 */
export function syncSplitVirtualSteps(project) {
  if (!project.pipeline_config) project.pipeline_config = {};
  if (!Array.isArray(project.steps_config)) project.steps_config = [];
  const rules = (project.pipeline_config.label_splits || []).filter(r => r && r.enabled !== false);
  const added = [];
  const removed = [];

  // 期望的虚拟步骤全集: label -> {ruleId, autoDisabled}
  // 多轮次规则展开为 前缀+区域名 (只有画完整的区域参与展开)
  const expected = new Map();
  const _drawnOnly = (list) => (list || []).filter(
    r => r && String(r.name || '').trim() && _validPolygon(r.polygon)
  );
  for (const rule of rules) {
    const drawn = { ...rule, regions: _drawnOnly(rule.regions) };
    // 每轮独立区域也只取画完整的（某轮全没画完 → 展开时该轮回退共享区域, 与后端同语义）
    if (rule.rounds && rule.rounds.enabled && rule.rounds.region_overrides) {
      const overrides = {};
      for (const [key, list] of Object.entries(rule.rounds.region_overrides)) {
        const valid = _drawnOnly(list);
        if (valid.length) overrides[key] = valid;
      }
      drawn.rounds = { ...rule.rounds, region_overrides: overrides };
    }
    for (const name of splitRuleStepNames(drawn)) {
      expected.set(name, { ruleId: rule.id, autoDisabled: false });
    }
    if (rule.unmatched === 'map' && String(rule.unmatched_label || '').trim()) {
      const ml = String(rule.unmatched_label).trim();
      if (!expected.has(ml)) expected.set(ml, { ruleId: rule.id, autoDisabled: true });
    }
  }

  // 孤儿清理（split_origin 步骤但已不在期望集合里）
  const orphans = project.steps_config.filter(
    s => s && s.split_origin && !(expected.has(s.label) && expected.get(s.label).ruleId === s.split_origin)
  );
  for (const step of orphans) {
    try { applyStepEnabledChange(project, step, false); } catch (_e) { /* 序列清理失败不阻断 */ }
    removed.push(step.label);
  }
  if (orphans.length) {
    const orphanIds = new Set(orphans.map(s => s.id));
    project.steps_config = project.steps_config.filter(s => !orphanIds.has(s.id));
  }

  // 补建缺失的虚拟步骤
  const existingLabels = new Set(project.steps_config.map(s => s.label));
  let nextId = project.steps_config.reduce((m, s) => Math.max(m, Number(s.id) || 0), 0) + 1;
  for (const [label, meta] of expected) {
    if (existingLabels.has(label)) {
      // 已存在: 只校正归属标记（规则 id 变了跟着更新）
      const st = project.steps_config.find(s => s.label === label);
      if (st && st.split_origin) st.split_origin = meta.ruleId;
      continue;
    }
    const step = {
      id: nextId++,
      label,
      displayLabel: label,
      enabled: !meta.autoDisabled,
      roi: null,
      threshold: 50,
      triggerEvent: null,
      min_frames: null,
      detection_type: 'dynamic',
      static_trigger_frames: 30,
      join_cycle: true,
      backup_for: null,
      default_pt: null,
      strict_order: false,
      accept_once: false,
      tracking_gone_confirm_frames: null,
      tracking_max_lost_seconds: 5.0,
      tracking_position_lock: false,
      count_mode: 'track',
      event_required_count: 1,
      event_gone_frames: 8,
      box_color: '',
      from_model: 'main',
      box_max_width: 0,
      box_max_height: 0,
      split_origin: meta.ruleId,
    };
    project.steps_config.push(step);
    if (!meta.autoDisabled) {
      try { applyStepEnabledChange(project, step, true); } catch (_e) { /* 序列追加失败不阻断 */ }
    }
    added.push(label);
  }
  return { added, removed };
}

/** 删除一条规则并级联清理它生成的虚拟步骤（调用方负责确认交互）。 */
export function removeSplitRule(project, ruleId) {
  const list = project?.pipeline_config?.label_splits;
  if (!Array.isArray(list)) return;
  const idx = list.findIndex(r => r && r.id === ruleId);
  if (idx >= 0) list.splice(idx, 1);
  syncSplitVirtualSteps(project);
}
