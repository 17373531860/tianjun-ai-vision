"""torch 推理设备解析与显存清理的统一出口 (2026-08 Apple MPS 支持).

背景: 开发机从 Ubuntu + NVIDIA 迁到 Apple Silicon MacBook 后, 原 auto 档
"有 CUDA 用 CUDA 否则 CPU" 会静默落到 CPU, M 芯片的 GPU (Metal / MPS 后端)
完全用不上. 这里统一 auto 档解析顺序为: cuda:0 > mps > cpu.

约定:
- 显式指定的设备字符串 (cuda:N / mps / cpu) 各处一律原样透传, 本模块只管 auto.
- FP16 半精度在 CUDA 与 MPS 上均可启用 (2026-08 放开; detect runners 守门为
  device.startswith(('cuda', 'mps'))), 仍受项目 use_half 开关控制 —— M5 Pro
  实测三路并发 24→37fps, conf 漂移 <0.03, 与 FP32 判定结果一致.
- TensorRT (.engine) 与 MPS 无关, 该格式仍只在 CUDA 机器可用.
- **MPS 读写锁** (2026-08-25 重设计, 治三工位帧率减半): torch 2.13 的 MPS 后端
  跨线程**并发推理是安全的** (M5 Pro 微基准 450 次并发 predict 零崩溃),
  2026-08-07 的 Metal 命令缓冲断言弑进程
  (`_MTLCommandBuffer addScheduledHandler: failed assertion`)
  真正的冲突面是 **推理 vs 资源管理**: 一个通道推理中另一个通道
  empty_mps_cache() / 模型加载释放 / gc 回收 MPS 张量。
  故推理走共享读锁 (mps_infer_guard, 多通道并发放行),
  资源管理走独占写锁 (mps_guard, 与所有推理互斥)。
  最初的全局互斥 RLock 把三路推理串行化, 每路 fps 从 24 掉到 13, 已废弃。
  CUDA 线程安全且多卡可分流, 两把守卫在 CUDA/CPU 机器都是 no-op.
"""
import threading
from contextlib import nullcontext


class _MPSRWLock:
    """写者优先的读写锁, 写者对同线程可重入.

    - 读者 = 推理 (predict/track + 锁内 .cpu() 搬运), 多通道并发放行;
    - 写者 = empty_cache / synchronize / 模型加载释放 / gc, 独占;
    - 写者重入: 释放路径持写锁期间内部还会调 empty_mps_cache()/synchronize_mps()
      (自身也拿写锁), 按持有线程直接放行;
    - 写者持锁线程内再进读区 (如加载路径里的预热 predict) 同样直接放行;
    - 写者优先: 有写者排队时新读者等待, 防止连续推理饿死释放/清理.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = None       # 持写锁线程 ident
        self._writer_depth = 0
        self._waiting_writers = 0

    def acquire_read(self):
        me = threading.get_ident()
        with self._cond:
            if self._writer == me:      # 写区内嵌读: 写锁已互斥一切
                self._writer_depth += 1
                return
            while self._writer is not None or self._waiting_writers:
                self._cond.wait()
            self._readers += 1

    def release_read(self):
        me = threading.get_ident()
        with self._cond:
            if self._writer == me:
                self._writer_depth -= 1
                return
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self):
        me = threading.get_ident()
        with self._cond:
            if self._writer == me:      # 写者重入
                self._writer_depth += 1
                return
            self._waiting_writers += 1
            try:
                while self._writer is not None or self._readers:
                    self._cond.wait()
            finally:
                self._waiting_writers -= 1
            self._writer = me
            self._writer_depth = 1

    def release_write(self):
        with self._cond:
            self._writer_depth -= 1
            if self._writer_depth == 0:
                self._writer = None
                self._cond.notify_all()


class _ReadCtx:
    __slots__ = ()

    def __enter__(self):
        _MPS_RW.acquire_read()
        return None

    def __exit__(self, *a):
        _MPS_RW.release_read()
        return False


class _WriteCtx:
    __slots__ = ()

    def __enter__(self):
        _MPS_RW.acquire_write()
        return None

    def __exit__(self, *a):
        _MPS_RW.release_write()
        return False


_MPS_RW = _MPSRWLock()
_READ_CTX = _ReadCtx()
_WRITE_CTX = _WriteCtx()


def mps_guard():
    """MPS 独占临界区 (写锁): 与所有通道的推理互斥, 其它机器返回 no-op.

    用于包住"释放模型 (del + gc) / 加载搬权重 / 预热 / empty_cache"这类
    会动 Metal 资源管理的整段操作, CUDA / CPU 机器零开销零行为变化.
    """
    return _WRITE_CTX if mps_available() else nullcontext()


def mps_infer_guard():
    """MPS 推理共享临界区 (读锁): 多通道并发推理放行, 只与 mps_guard 互斥.

    包住 predict/track + 锁内 Results.cpu() 搬运; 其它机器返回 no-op.
    """
    return _READ_CTX if mps_available() else nullcontext()


def mps_available() -> bool:
    """Apple MPS 后端是否可用 (非 Apple Silicon / 老 torch 均返回 False)."""
    try:
        import torch
        mps = getattr(torch.backends, "mps", None)
        return bool(mps is not None and mps.is_available())
    except Exception:
        return False


def resolve_auto_device() -> str:
    """auto 档设备解析: cuda:0 > mps > cpu."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda:0"
    except ImportError:
        return "cpu"
    if mps_available():
        return "mps"
    return "cpu"


def empty_mps_cache() -> None:
    """MPS 版 empty_cache, 与 torch.cuda.empty_cache() 对应; 不可用时静默.

    持写锁: 另一通道推理中调它会撞 Metal 断言弑进程 (见模块 docstring).
    """
    try:
        import torch
        if mps_available():
            with _WRITE_CTX:
                torch.mps.empty_cache()
    except Exception:
        pass


def synchronize_mps() -> None:
    """MPS 版 synchronize, 与 torch.cuda.synchronize() 对应; 不可用时静默.

    持写锁 (全设备同步本质是资源管理操作)。⚠️ 禁止在持读锁 (mps_infer_guard)
    的推理路径里调用 — 读锁线程等写锁会与"写者等读者清零"互相等死.
    """
    try:
        import torch
        if mps_available():
            with _WRITE_CTX:
                torch.mps.synchronize()
    except Exception:
        pass
