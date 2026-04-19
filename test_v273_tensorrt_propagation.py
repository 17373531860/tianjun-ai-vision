"""v2.7.3 TensorRT 双工位 imgsz 修复 - 真集成测试

跑法: cd 到项目根目录, python test_v273_tensorrt_propagation.py

测试不依赖 mock, 用真实的 ChannelManager + 真实 ultralytics YOLO 模型,
覆盖 v2.7.3 所有三处属性传播路径 + engine metadata 解析函数.
"""
import os
import sys
import struct
import json
import tempfile
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


# 找一个本地 best.pt 模型
CANDIDATES = [
    os.path.join(ROOT, "best.pt"),
    os.path.join(ROOT, "backend/uploads/models/2e80fd1996764ba9b96036e904f40097_best.pt"),
]
PT_MODEL = next((p for p in CANDIDATES if os.path.exists(p)), None)


def hr(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def test_a_engine_metadata_parser():
    """Test A: _read_engine_metadata_imgsz 真函数解析人造 engine 头"""
    hr("Test A: _read_engine_metadata_imgsz 直读 .engine 元数据")
    from backend.api.source import _read_engine_metadata_imgsz

    fails = []

    # A1: 标准 list 形式 imgsz
    with tempfile.NamedTemporaryFile(suffix=".engine", delete=False) as f:
        meta = json.dumps({"imgsz": [960, 960], "stride": 32}).encode("utf-8")
        f.write(struct.pack("<I", len(meta)))
        f.write(meta)
        f.write(b"\x00" * 1024)  # 模拟后面的 engine 主体
        p = f.name
    got = _read_engine_metadata_imgsz(p)
    print(f"  A1 list[960,960]  -> {got} (expect 960)")
    if got != 960: fails.append("A1")
    os.unlink(p)

    # A2: tuple 字符串形式（早期 ultralytics 写法）
    with tempfile.NamedTemporaryFile(suffix=".engine", delete=False) as f:
        meta = json.dumps({"imgsz": "(640, 960)"}).encode("utf-8")
        f.write(struct.pack("<I", len(meta)))
        f.write(meta)
        p = f.name
    got = _read_engine_metadata_imgsz(p)
    print(f"  A2 str '(640,960)' -> {got} (expect 960)")
    if got != 960: fails.append("A2")
    os.unlink(p)

    # A3: 整数形式
    with tempfile.NamedTemporaryFile(suffix=".engine", delete=False) as f:
        meta = json.dumps({"imgsz": 1280}).encode("utf-8")
        f.write(struct.pack("<I", len(meta)))
        f.write(meta)
        p = f.name
    got = _read_engine_metadata_imgsz(p)
    print(f"  A3 int 1280       -> {got} (expect 1280)")
    if got != 1280: fails.append("A3")
    os.unlink(p)

    # A4: 损坏文件 (前 4 字节是垃圾)
    with tempfile.NamedTemporaryFile(suffix=".engine", delete=False) as f:
        f.write(b"\xff\xff\xff\xff" + b"random binary garbage" * 50)
        p = f.name
    got = _read_engine_metadata_imgsz(p)
    print(f"  A4 损坏 engine    -> {got} (expect None)")
    if got is not None: fails.append("A4")
    os.unlink(p)

    # A5: 没有 imgsz 字段
    with tempfile.NamedTemporaryFile(suffix=".engine", delete=False) as f:
        meta = json.dumps({"stride": 32, "names": ["a", "b"]}).encode("utf-8")
        f.write(struct.pack("<I", len(meta)))
        f.write(meta)
        p = f.name
    got = _read_engine_metadata_imgsz(p)
    print(f"  A5 无 imgsz 字段  -> {got} (expect None)")
    if got is not None: fails.append("A5")
    os.unlink(p)

    return fails


def test_b_load_shared_model_propagation():
    """Test B: load_shared_model 双工位属性传播 (核心 bug 修复路径)"""
    hr("Test B: load_shared_model 把 ch0 的 imgsz/task/native 复制到 ch1")
    if PT_MODEL is None:
        print("  SKIP: 找不到 best.pt 模型, 跳过")
        return ["B-SKIP"]

    print(f"  使用模型: {PT_MODEL}")
    from backend.api.channel_manager import ChannelManager

    cm = ChannelManager()
    cm.set_channel_count(2)
    print(f"  通道数: {cm.channel_count}, active: {cm.active_channels()}")

    # 重置 ch1 属性到 "默认未传播" 状态, 验证修复确实复制了过去
    ch0, ch1 = cm.channels[0], cm.channels[1]
    ch1._model_imgsz = -999
    ch1._is_native_pytorch = None
    ch1.model_task = "WRONG"
    ch1._original_pt_path = "WRONG"

    print("  调用 load_shared_model(cuda:0)...")
    ok = cm.load_shared_model(PT_MODEL, device="cuda:0")
    print(f"  load_shared_model returned: {ok}")
    if not ok:
        print("  FAIL: 模型加载失败")
        return ["B-LOAD"]

    fails = []
    print(f"  ch0._model_imgsz = {ch0._model_imgsz}")
    print(f"  ch1._model_imgsz = {ch1._model_imgsz}")
    if ch0._model_imgsz != ch1._model_imgsz or ch1._model_imgsz == -999:
        fails.append("B-imgsz")

    print(f"  ch0._is_native_pytorch = {ch0._is_native_pytorch}")
    print(f"  ch1._is_native_pytorch = {ch1._is_native_pytorch}")
    if ch0._is_native_pytorch != ch1._is_native_pytorch:
        fails.append("B-native")

    print(f"  ch0.model_task = {ch0.model_task}")
    print(f"  ch1.model_task = {ch1.model_task}")
    if ch0.model_task != ch1.model_task or ch1.model_task == "WRONG":
        fails.append("B-task")

    print(f"  ch0._original_pt_path = {ch0._original_pt_path}")
    print(f"  ch1._original_pt_path = {ch1._original_pt_path}")
    if ch0._original_pt_path != ch1._original_pt_path or ch1._original_pt_path == "WRONG":
        fails.append("B-pt_path")

    print(f"  ch1.model is ch0.model? {ch1.model is ch0.model}")
    if ch1.model is not ch0.model:
        fails.append("B-shared")
    return fails


def test_c_load_model_for_channel_cache_hit():
    """Test C: load_model_for_channel 第二次同路径走 cache hit 路径, 必须复制属性"""
    hr("Test C: load_model_for_channel cache-hit 路径属性传播")
    if PT_MODEL is None:
        print("  SKIP")
        return ["C-SKIP"]

    from backend.api.channel_manager import ChannelManager
    cm = ChannelManager()
    cm.set_channel_count(2)

    print(f"  ch0 加载 (NEW)...")
    cm.load_model_for_channel(0, PT_MODEL, "cuda:0")
    ch0 = cm.channels[0]
    print(f"  ch0._model_imgsz={ch0._model_imgsz}, task={ch0.model_task}, "
          f"native={ch0._is_native_pytorch}")

    # 把 ch1 重置到默认值, 模拟"刚创建未加载"
    ch1 = cm.channels[1]
    ch1._model_imgsz = -999
    ch1._is_native_pytorch = None
    ch1.model_task = "WRONG"
    ch1._original_pt_path = "WRONG"

    print(f"  ch1 加载同路径 (应走 cache hit)...")
    cm.load_model_for_channel(1, PT_MODEL, "cuda:0")

    fails = []
    print(f"  ch1._model_imgsz = {ch1._model_imgsz} (期望 = ch0 的 {ch0._model_imgsz})")
    if ch1._model_imgsz != ch0._model_imgsz or ch1._model_imgsz == -999:
        fails.append("C-imgsz")
    print(f"  ch1.model_task = {ch1.model_task}")
    if ch1.model_task != ch0.model_task or ch1.model_task == "WRONG":
        fails.append("C-task")
    print(f"  ch1._original_pt_path = {ch1._original_pt_path}")
    if ch1._original_pt_path != ch0._original_pt_path or ch1._original_pt_path == "WRONG":
        fails.append("C-pt_path")
    print(f"  ch1.model is ch0.model? {ch1.model is ch0.model}")
    if ch1.model is not ch0.model:
        fails.append("C-shared")
    return fails


def test_d_propagate_model_after_resize():
    """Test D: 单→双工位升级时 _propagate_model 把 ch0 属性传给 ch1"""
    hr("Test D: _propagate_model 单工位升级到双工位时属性传播")
    if PT_MODEL is None:
        print("  SKIP")
        return ["D-SKIP"]

    from backend.api.channel_manager import ChannelManager
    cm = ChannelManager()
    print(f"  起始通道数: {cm.channel_count}")

    print(f"  ch0 单工位加载模型...")
    cm.load_shared_model(PT_MODEL, device="cuda:0")
    ch0 = cm.channels[0]
    print(f"  ch0._model_imgsz={ch0._model_imgsz}, task={ch0.model_task}")

    print(f"  升到双工位...")
    cm.set_channel_count(2)
    ch1 = cm.channels[1]
    # 此时 ch1 是新 VideoSourceManager, model 应为 None
    print(f"  升级后 ch1.model is None? {ch1.model is None}")
    print(f"  升级后 ch1._model_imgsz={ch1._model_imgsz}")

    print(f"  调用 _propagate_model(1)...")
    cm._propagate_model(1)

    fails = []
    print(f"  ch1.model is ch0.model? {ch1.model is ch0.model}")
    if ch1.model is not ch0.model:
        fails.append("D-shared")
    print(f"  ch1._model_imgsz = {ch1._model_imgsz} (期望 {ch0._model_imgsz})")
    if ch1._model_imgsz != ch0._model_imgsz:
        fails.append("D-imgsz")
    print(f"  ch1.model_task = {ch1.model_task}")
    if ch1.model_task != ch0.model_task:
        fails.append("D-task")
    print(f"  ch1._original_pt_path = {ch1._original_pt_path}")
    if ch1._original_pt_path != ch0._original_pt_path:
        fails.append("D-pt_path")
    return fails


def main():
    print("=" * 70)
    print("v2.7.3 TensorRT 双工位 imgsz 修复 - 真集成测试")
    print("=" * 70)
    print(f"Python: {sys.version.split()[0]}")
    try:
        import torch
        print(f"torch: {torch.__version__} cuda={torch.cuda.is_available()}")
    except Exception:
        pass
    try:
        import ultralytics
        print(f"ultralytics: {ultralytics.__version__}")
    except Exception:
        pass
    print(f"PT 模型: {PT_MODEL or '未找到'}")

    all_fails = []
    for name, fn in [
        ("A", test_a_engine_metadata_parser),
        ("B", test_b_load_shared_model_propagation),
        ("C", test_c_load_model_for_channel_cache_hit),
        ("D", test_d_propagate_model_after_resize),
    ]:
        try:
            fails = fn() or []
        except Exception as e:
            traceback.print_exc()
            fails = [f"{name}-EXC:{e}"]
        all_fails.extend(fails)

    hr("总结")
    if not all_fails:
        print("  ✓ 全部通过")
        return 0
    print(f"  失败 {len(all_fails)} 项: {all_fails}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
