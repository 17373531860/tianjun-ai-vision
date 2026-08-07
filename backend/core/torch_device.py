"""torch 推理设备解析与显存清理的统一出口 (2026-08 Apple MPS 支持).

背景: 开发机从 Ubuntu + NVIDIA 迁到 Apple Silicon MacBook 后, 原 auto 档
"有 CUDA 用 CUDA 否则 CPU" 会静默落到 CPU, M 芯片的 GPU (Metal / MPS 后端)
完全用不上. 这里统一 auto 档解析顺序为: cuda:0 > mps > cpu.

约定:
- 显式指定的设备字符串 (cuda:N / mps / cpu) 各处一律原样透传, 本模块只管 auto.
- FP16 半精度仍只在 CUDA 上启用 (detect runners 已有 device.startswith('cuda')
  守门), MPS 走 FP32 —— 保证与有卡机器的数值口径可比.
- TensorRT (.engine) 与 MPS 无关, 该格式仍只在 CUDA 机器可用.
- **MPS 全局互斥** (2026-08-07 稳定性长跑实测): torch 的 MPS 后端对跨线程并发
  不安全 —— 双通道各自的推理线程同时 predict, 或一个通道推理中另一个通道
  empty_mps_cache(), 都会触发 Metal 命令缓冲断言直接弑进程
  (`_MTLCommandBuffer addScheduledHandler: failed assertion`).
  所有 MPS 触点 (predict / warmup / release / empty_cache) 必须持 MPS_LOCK 串行;
  CUDA 线程安全且多卡可分流, 不受此锁影响.
"""
import threading
from contextlib import nullcontext

# 进程级 MPS 串行锁 — 见模块 docstring. 单 GPU 本就吞吐共享, 串行不损总帧率.
# RLock: 释放路径持锁期间内部还会调 empty_mps_cache() (自身也拿锁), 需可重入.
MPS_LOCK = threading.RLock()


def mps_guard():
    """MPS 临界区上下文: MPS 机器返回全局串行锁, 其它机器返回 no-op.

    用于包住"释放模型 (del + gc) / 预热"这类会动 Metal 资源的整段操作,
    CUDA / CPU 机器零开销零行为变化.
    """
    return MPS_LOCK if mps_available() else nullcontext()


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

    持 MPS_LOCK: 另一通道推理中调它会撞 Metal 断言弑进程 (见模块 docstring).
    """
    try:
        import torch
        if mps_available():
            with MPS_LOCK:
                torch.mps.empty_cache()
    except Exception:
        pass


def synchronize_mps() -> None:
    """MPS 版 synchronize, 与 torch.cuda.synchronize() 对应; 不可用时静默."""
    try:
        import torch
        if mps_available():
            with MPS_LOCK:
                torch.mps.synchronize()
    except Exception:
        pass
