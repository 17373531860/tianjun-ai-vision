"""B5 扫码待绑队列上限 + B7 模型转换队列上限 的零风险单元测试。

只验证"队列到顶不再无限涨"的旁路保护, 不触发正常业务路径。
"""


# ============================== B5 待绑队列上限 ==============================
def test_b5_pending_queue_bounded_drops_oldest():
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    cap = mgr._MAX_PENDING_QUEUE
    ch = 0

    # 正常压入 cap 个: 不丢
    for i in range(cap):
        mgr._enqueue_pending(ch, i)
    assert len(mgr._pending_queue[ch]) == cap
    assert mgr._pending_queue[ch][0] == 0  # 最旧还在

    # 再压一个: 触顶, 丢最旧(0), 新的(cap)进尾
    mgr._enqueue_pending(ch, cap)
    assert len(mgr._pending_queue[ch]) == cap  # 仍然封顶
    assert mgr._pending_queue[ch][0] == 1     # 0 被丢
    assert mgr._pending_queue[ch][-1] == cap  # 新件在尾


def test_b5_normal_cadence_never_caps():
    """扫一件检一件: 队列长期 0~1, 不触发上限逻辑。"""
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    ch = 1
    for i in range(50):
        mgr._enqueue_pending(ch, i)
        # 模拟检测消费(出队)
        mgr._pending_queue[ch].pop(0)
    assert len(mgr._pending_queue[ch]) == 0


# ============================== B7 转换队列上限 ==============================
def test_b7_conversion_queue_has_cap_constant():
    """B7: 模型转换队列上限常量存在且为正整数(具体入队拒绝逻辑在 models.py)。"""
    import backend.api.models as models_api
    cap = getattr(models_api, "_MAX_CONVERSION_QUEUE", None)
    assert isinstance(cap, int) and cap > 0
