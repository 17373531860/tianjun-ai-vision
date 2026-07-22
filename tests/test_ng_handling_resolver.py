"""v3.44 NG 处置统一模型: resolve_ng_handling 迁移矩阵单测.

纯函数, 不碰 DB / 不起后端. 覆盖:
  - 空配置 → 全默认 (零差异)
  - legacy 键合成 (v3.23 补做 / v3.32 违序提示 / v3.43 实时NG / v3.44 收尾防呆)
  - 新块优先于 legacy 键
  - 枚举/类型净化
"""
from backend.api.source_project_config_apply import resolve_ng_handling


def test_空配置_全默认零差异():
    r = resolve_ng_handling({})
    assert r == {
        'violation': 'none', 'violation_event_id': None,
        'missing_step': 'ng', 'hold_timeout_s': 120.0, 'hold_event_id': None,
        'short_count': 'ng',
        'gate_enabled': False, 'gate_steps': [], 'gate_event_id': None,
    }


def test_legacy_补做策略_开补步骤补数量():
    r = resolve_ng_handling({'ng_remediation': {
        'enabled': True, 'allow_step': True, 'allow_count': True}})
    assert r['missing_step'] == 'ack'
    assert r['short_count'] == 'ack'


def test_legacy_补做策略_只开补数量():
    r = resolve_ng_handling({'ng_remediation': {
        'enabled': True, 'allow_step': False, 'allow_count': True}})
    assert r['missing_step'] == 'ng'
    assert r['short_count'] == 'ack'


def test_legacy_补做策略_未启用_子开关无效():
    r = resolve_ng_handling({'ng_remediation': {
        'enabled': False, 'allow_step': True, 'allow_count': True}})
    assert r['missing_step'] == 'ng'
    assert r['short_count'] == 'ng'


def test_legacy_收尾防呆_挂起压过补步骤():
    """hold + 补步骤同时开 (SY3 现场态) → 挂起档 (超时链上补做语义仍在)."""
    r = resolve_ng_handling({
        'closing_guard': {'hold_enabled': True, 'hold_timeout_s': 60, 'event_id': 4},
        'ng_remediation': {'enabled': True, 'allow_step': True, 'allow_count': True},
    })
    assert r['missing_step'] == 'hold'
    assert r['hold_timeout_s'] == 60.0
    assert r['hold_event_id'] == 4
    assert r['short_count'] == 'ack'


def test_legacy_收尾防呆_数量门_事件同值分流():
    """老单一提示事件迁移后, 门/挂起两边都拿到同一事件 (语义不丢)."""
    r = resolve_ng_handling({'closing_guard': {
        'gate_enabled': True, 'gate_steps': ['放油嘴包', '封箱', ''],
        'hold_enabled': True, 'event_id': '4'}})
    assert r['gate_enabled'] is True
    assert r['gate_steps'] == ['放油嘴包', '封箱']  # 空串净化
    assert r['gate_event_id'] == 4 and r['hold_event_id'] == 4


def test_legacy_实时NG_带违序提示事件():
    r = resolve_ng_handling({
        'instant_ng_on_violation': True,
        'strict_order_violation_event_id': 3,
    })
    assert r['violation'] == 'instant_ng'
    assert r['violation_event_id'] == 3


def test_legacy_仅违序提示():
    r = resolve_ng_handling({'strict_order_violation_event_id': 3})
    assert r['violation'] == 'hint'
    assert r['violation_event_id'] == 3


def test_新块优先于legacy键():
    r = resolve_ng_handling({
        'ng_handling': {'missing_step': 'hold', 'hold_timeout_s': 30,
                        'violation': 'hint', 'violation_event_id': 5,
                        'gate_enabled': True, 'gate_steps': ['封箱'],
                        'gate_event_id': 6, 'hold_event_id': 7},
        # 下面全是应被忽略的旧键
        'ng_remediation': {'enabled': True, 'allow_count': True},
        'instant_ng_on_violation': True,
        'closing_guard': {'hold_enabled': False, 'event_id': 9},
    })
    assert r['missing_step'] == 'hold' and r['hold_timeout_s'] == 30.0
    assert r['violation'] == 'hint' and r['violation_event_id'] == 5
    assert r['gate_event_id'] == 6 and r['hold_event_id'] == 7
    assert r['short_count'] == 'ng'  # 新块没写 = 默认, 不吃旧键


def test_枚举与类型净化():
    r = resolve_ng_handling({'ng_handling': {
        'missing_step': '瞎写', 'violation': 'yes', 'short_count': 0,
        'hold_timeout_s': 'abc', 'violation_event_id': 'junk',
        'gate_steps': None}})
    assert r['missing_step'] == 'ng'
    assert r['violation'] == 'none'
    assert r['short_count'] == 'ng'
    assert r['hold_timeout_s'] == 120.0
    assert r['violation_event_id'] is None
    assert r['gate_steps'] == []
