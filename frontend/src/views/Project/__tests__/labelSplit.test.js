// labelSplit 纯函数单测 — v3.59.0a 步骤 id 冲突自愈（东莞群光现场）
import { describe, it, expect } from 'vitest';
import { dedupeStepIds } from '../labelSplit';

describe('dedupeStepIds', () => {
  it('现场同款: 换模型后主步骤(包装盒)与拆分虚拟步骤(拿取大电池)同 id → 后者重编', () => {
    // 旧模型 3 标签时代: 拆分虚拟步骤分到 id=4; 换 4 标签模型后主步骤 包装盒 也是 4
    const project = {
      steps_config: [
        { id: 1, label: '拿取电池', from_model: 'main' },
        { id: 2, label: '拿取说明书', from_model: 'main' },
        { id: 3, label: '电池盒', from_model: 'main' },
        { id: 4, label: '包装盒', from_model: 'main' },
        { id: 4, label: '拿取大电池', split_origin: 'ls_1' },
        { id: 5, label: '拿取小电池', split_origin: 'ls_1' },
      ],
    };
    const renamed = dedupeStepIds(project);
    expect(renamed).toEqual([{ label: '拿取大电池', oldId: 4, newId: 6 }]);
    // 主步骤保住原 id; 虚拟步骤重编到 max+1; 全表 id 唯一
    const ids = project.steps_config.map(s => s.id);
    expect(ids).toEqual([1, 2, 3, 4, 6, 5]);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('无冲突时零差异、多重冲突逐个重编', () => {
    const clean = { steps_config: [{ id: 1, label: 'a' }, { id: 2, label: 'b' }] };
    expect(dedupeStepIds(clean)).toEqual([]);
    expect(clean.steps_config.map(s => s.id)).toEqual([1, 2]);

    const messy = {
      steps_config: [
        { id: 1, label: 'a' }, { id: 1, label: 'b' }, { id: 1, label: 'c' },
      ],
    };
    const renamed = dedupeStepIds(messy);
    expect(renamed.map(r => r.newId)).toEqual([2, 3]);
    expect(messy.steps_config.map(s => s.id)).toEqual([1, 2, 3]);
  });

  it('空/缺配置与非法 id 不炸', () => {
    expect(dedupeStepIds({})).toEqual([]);
    expect(dedupeStepIds({ steps_config: [{ id: null, label: 'x' }, { label: 'y' }] })).toEqual([]);
  });
});
