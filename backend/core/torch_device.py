"""torch 推理设备解析与显存清理的统一出口 (2026-08 Apple MPS 支持).

背景: 开发机从 Ubuntu + NVIDIA 迁到 Apple Silicon MacBook 后, 原 auto 档
"有 CUDA 用 CUDA 否则 CPU" 会静默落到 CPU, M 芯片的 GPU (Metal / MPS 后端)
完全用不上. 这里统一 auto 档解析顺序为: cuda:0 > mps > cpu.

约定:
- 显式指定的设备字符串 (cuda:N / mps / cpu) 各处一律原样透传, 本模块只管 auto.
- FP16 半精度仍只在 CUDA 上启用 (detect runners 已有 device.startswith('cuda')
  守门), MPS 走 FP32 —— 保证与有卡机器的数值口径可比.
- TensorRT (.engine) 与 MPS 无关, 该格式仍只在 CUDA 机器可用.
"""


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
    """MPS 版 empty_cache, 与 torch.cuda.empty_cache() 对应; 不可用时静默."""
    try:
        import torch
        if mps_available():
            torch.mps.empty_cache()
    except Exception:
        pass


def synchronize_mps() -> None:
    """MPS 版 synchronize, 与 torch.cuda.synchronize() 对应; 不可用时静默."""
    try:
        import torch
        if mps_available():
            torch.mps.synchronize()
    except Exception:
        pass
