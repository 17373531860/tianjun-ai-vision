# -*- coding: utf-8 -*-
"""每通道落库线程 (v3.38, RFC: docs/rfc/收尾持久化出推理线程_设计方案_RFC.md)。

背景 (川南"框冻结"事故链的治本刀):
  record_step / end_cycle / 步骤对账 / 空周期作废的数据库提交跑在推理线程里,
  任何别处 (MES 推送 / 清理 / 导出) 握住 SQLite 写锁, 推理线程就在 commit 干等
  (busy_timeout 上限 15s) → 检测框冻结。前两刀消掉了已知长锁持有方, 本刀把
  帧循环里的写库整体搬出来: 再出现新的持锁方, 卡的也是本线程, 检测不受影响。

设计 (决策/持久化分离):
  - 判定、内存状态机、计数器全部留在推理线程 (原地不动)
  - 写库 + 依赖"已提交行"的副作用 (MES on_cycle_end / 插件 cycle_end hook /
    三个协调器 / 扫码器联动) 打包成作业, 进本线程按提交先后严格 FIFO 执行
  - 作业携带快照 (值拷贝), 不在工作线程里读推理线程的活状态

不变量:
  - 同通道作业顺序 == 提交顺序 (步骤插入 → 对账 → 周期收尾, FIFO 天然保序)
  - 单作业异常隔离: 回滚 + 留痕 + 继续下一件, 线程永不退出
  - 队列打满 (默认 2000) → 退化为调用方同步执行 (数据安全优先于延迟), 大声留痕
  - `TIANJUN_SYNC_PERSIST=1` 全局回退同步执行 (现场兜底开关; 测试套件也用它
    保证"提交后立刻可查"的既有断言语义)
"""
import os
import queue
import threading
import time
import traceback

from backend.core import debug_center

# 队列上限: 正常一个周期 ~几条作业, 2000 意味着积压数百个周期才触发同步退化
_QUEUE_MAX = 2000


def sync_mode() -> bool:
    """全局同步回退开关 (环境变量, 进程级)。"""
    return os.environ.get("TIANJUN_SYNC_PERSIST", "0") == "1"


class PersistWorker:
    """单通道 FIFO 落库线程。作业 = (描述, 无参可调用)。"""

    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        self._q: "queue.Queue" = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread = None
        self._start_lock = threading.Lock()
        self._jobs_done = 0
        self._jobs_failed = 0
        self._inline_fallbacks = 0

    # ==================== 提交与执行 ====================
    def submit(self, desc: str, fn):
        """提交作业。同步模式 / 队列打满 → 原地执行 (保数据不保延迟)。"""
        if sync_mode():
            self._run_job(desc, fn)
            return
        self._ensure_thread()
        try:
            self._q.put_nowait((desc, fn))
        except queue.Full:
            self._inline_fallbacks += 1
            print(f"[Persist] ch{self.channel_id} 落库队列打满({_QUEUE_MAX}), "
                  f"{desc} 退化为同步执行 (检查落库线程是否被长锁拖住)", flush=True)
            debug_center.dbg("backend.session", "!!! 落库队列打满退化同步",
                             f"channel={self.channel_id} job={desc}")
            self._run_job(desc, fn)

    def flush(self, timeout: float = 10.0) -> bool:
        """等队列排空 (含正在执行的作业)。返回 False = 超时仍有积压。

        用哨兵作业置事件的方式实现 FIFO 语义的 flush:
        哨兵被执行 == 它之前的所有作业都已完成。
        """
        if sync_mode() or self._thread is None or not self._thread.is_alive():
            return True
        done = threading.Event()
        try:
            self._q.put((f"__flush_ch{self.channel_id}__", done.set), timeout=timeout)
        except queue.Full:
            return False
        ok = done.wait(timeout)
        if not ok:
            print(f"[Persist] ch{self.channel_id} flush 超时({timeout}s), "
                  f"仍有 ~{self._q.qsize()} 件积压 (疑似写锁竞争)", flush=True)
            debug_center.dbg("backend.session", "!!! 落库 flush 超时",
                             f"channel={self.channel_id} backlog={self._q.qsize()}")
        return ok

    def stats(self) -> dict:
        return {
            "backlog": self._q.qsize(),
            "done": self._jobs_done,
            "failed": self._jobs_failed,
            "inline_fallbacks": self._inline_fallbacks,
            "alive": bool(self._thread and self._thread.is_alive()),
        }

    # ==================== 内部 ====================
    def _ensure_thread(self):
        t = self._thread
        if t is not None and t.is_alive():
            return
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop, name=f"persist-ch{self.channel_id}", daemon=True)
            self._thread.start()

    def _loop(self):
        print(f"[Persist] ch{self.channel_id} 落库线程启动", flush=True)
        while True:
            desc, fn = self._q.get()
            self._run_job(desc, fn)
            self._q.task_done()

    def _run_job(self, desc: str, fn):
        t0 = time.time()
        try:
            fn()
            self._jobs_done += 1
        except Exception as e:
            self._jobs_failed += 1
            print(f"[Persist] ch{self.channel_id} 作业 {desc} 异常(已隔离): {e}", flush=True)
            traceback.print_exc()
            debug_center.dbg("backend.session", "!!! 落库作业异常",
                             f"channel={self.channel_id} job={desc} err={e}")
        else:
            elapsed_ms = (time.time() - t0) * 1000
            # 单作业超 3s = 疑似锁竞争; 现在只拖累落库线程不拖检测, 但仍留痕指认
            if elapsed_ms > 3000:
                print(f"[Persist/Slow] ch{self.channel_id} 作业 {desc} 耗时 "
                      f"{elapsed_ms:.0f}ms (疑似写锁竞争, 检测不受影响)", flush=True)
                debug_center.dbg("backend.session", "落库作业慢(疑似锁等待)",
                                 f"channel={self.channel_id} job={desc} ms={elapsed_ms:.0f}")
