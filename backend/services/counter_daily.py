"""
自定义计数器按日增量记账 (每日短信日报配套, v3.46+)

背景:
  运行时计数器只有"当前累计值"(内存 + counters/*.json + session 快照),
  没有"某日增加了多少"的历史 —— 短信日报要报"今天数据", 需要精确日增量。

语义约定:
  - 只累计正向增量 (delta > 0): 清零 / 重置 / 手动改小不扣减, 符合产量计数直觉
  - 键 = (stat_date, project_id, channel_id, counter_name), 跨午夜自动落到新键

线程模型 (硬约束: 计数器增量在事件触发热路径上, 绝不能逐次写 DB):
  - record_increment() 只在锁内更新内存日桶, 微秒级
  - 懒启动的 daemon flush 线程每 FLUSH_INTERVAL_S 秒把脏桶 upsert 进
    counter_daily_stats; 崩溃最多丢一个间隔的增量 (与计数器快照惯例一致)
  - flush_now() 供日报聚合 / 试发 / 测试强制刷盘
"""
import threading
import datetime as _dt

FLUSH_INTERVAL_S = 30

_lock = threading.Lock()
# {(date, project_id, channel_id, name): pending_delta}
_bucket = {}
_flush_thread = None
_stop_event = threading.Event()


def record_increment(project_id, channel_id, counter_name, delta):
    """挂账一笔计数器增量。热路径安全: 只碰内存, 永不抛异常。"""
    try:
        delta = int(delta)
    except Exception:
        return
    if delta <= 0 or not counter_name:
        return
    key = (_dt.date.today(), int(project_id or 0), int(channel_id or 0), str(counter_name))
    with _lock:
        _bucket[key] = _bucket.get(key, 0) + delta
    _ensure_flush_thread()


def record_for_host(host, counter_name, delta):
    """从 VSM host 提取 project_id/channel_id 后挂账。任何异常吞掉, 不影响主流程。"""
    try:
        project_cfg = getattr(host, 'project_config', None) or {}
        project_id = project_cfg.get('id') or 0
        channel_id = getattr(host, 'channel_id', 0) or 0
        record_increment(project_id, channel_id, counter_name, delta)
    except Exception:
        pass


def flush_now():
    """把内存日桶全部 upsert 进 DB。失败的条目留在桶里等下轮。"""
    with _lock:
        if not _bucket:
            return
        pending = dict(_bucket)
        _bucket.clear()

    failed = {}
    try:
        from backend.db.database import SessionLocal
        from backend.models.notify_models import CounterDailyStat
        db = SessionLocal()
        try:
            for (stat_date, project_id, channel_id, name), delta in pending.items():
                try:
                    row = db.query(CounterDailyStat).filter(
                        CounterDailyStat.stat_date == stat_date,
                        CounterDailyStat.project_id == project_id,
                        CounterDailyStat.channel_id == channel_id,
                        CounterDailyStat.counter_name == name,
                    ).first()
                    if row:
                        row.delta = (row.delta or 0) + delta
                    else:
                        db.add(CounterDailyStat(
                            stat_date=stat_date, project_id=project_id,
                            channel_id=channel_id, counter_name=name, delta=delta,
                        ))
                    db.commit()
                except Exception as e:
                    db.rollback()
                    failed[(stat_date, project_id, channel_id, name)] = delta
                    print(f"[CounterDaily] upsert 失败 {name}: {e}")
        finally:
            db.close()
    except Exception as e:
        failed = pending
        print(f"[CounterDaily] flush 失败(整批回桶): {e}")

    if failed:
        with _lock:
            for key, delta in failed.items():
                _bucket[key] = _bucket.get(key, 0) + delta


def query_daily(stat_date, project_id=None, channel_id=None):
    """查询某日各计数器的增量合计, 返回 {counter_name: delta}。

    project_id / channel_id 为 None 时跨该维度求和 (汇总模式)。
    调用前建议先 flush_now() 把内存桶算进去。
    """
    from sqlalchemy import func as _f
    from backend.db.database import SessionLocal
    from backend.models.notify_models import CounterDailyStat

    db = SessionLocal()
    try:
        q = db.query(
            CounterDailyStat.counter_name,
            _f.sum(CounterDailyStat.delta),
        ).filter(CounterDailyStat.stat_date == stat_date)
        if project_id is not None:
            q = q.filter(CounterDailyStat.project_id == int(project_id))
        if channel_id is not None:
            q = q.filter(CounterDailyStat.channel_id == int(channel_id))
        return {name: int(total or 0) for name, total in q.group_by(CounterDailyStat.counter_name).all()}
    finally:
        db.close()


def _ensure_flush_thread():
    global _flush_thread
    if _flush_thread is not None and _flush_thread.is_alive():
        return
    with _lock:
        if _flush_thread is not None and _flush_thread.is_alive():
            return
        _flush_thread = threading.Thread(
            target=_flush_loop, name="counter-daily-flush", daemon=True)
        _flush_thread.start()


def _flush_loop():
    while not _stop_event.wait(FLUSH_INTERVAL_S):
        try:
            flush_now()
        except Exception as e:
            print(f"[CounterDaily] flush 线程异常: {e}")


def stop():
    """关机时调用: 停线程 + 最后一次刷盘。"""
    _stop_event.set()
    try:
        flush_now()
    except Exception:
        pass
