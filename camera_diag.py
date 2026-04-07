"""
摄像头诊断脚本 - 检测 USB 速度、支持格式、各格式实际帧率
用法: python camera_diag.py [摄像头编号, 默认0]
"""
import cv2
import time
import sys
import platform

device = int(sys.argv[1]) if len(sys.argv) > 1 else 0
WIDTH, HEIGHT = 1280, 720
TEST_FRAMES = 60

def fourcc_str(cap):
    fc = int(cap.get(cv2.CAP_PROP_FOURCC))
    if fc == 0:
        return "????"
    return "".join([chr((fc >> (8 * i)) & 0xFF) for i in range(4)])

def test_format(device, fourcc_code, label, backend=None):
    """测试指定格式的实际帧率"""
    try:
        if backend is not None:
            cap = cv2.VideoCapture(device, backend)
        elif platform.system() == "Windows":
            cap = cv2.VideoCapture(device, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(device)
        
        if not cap.isOpened():
            return None, None, "打开失败"
        
        if fourcc_code:
            cap.set(cv2.CAP_PROP_FOURCC, fourcc_code)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, 30)
        
        actual_cc = fourcc_str(cap)
        reported_fps = cap.get(cv2.CAP_PROP_FPS)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # 丢弃前几帧(预热)
        for _ in range(5):
            cap.read()
        
        # 计时读取
        t0 = time.perf_counter()
        ok_count = 0
        for _ in range(TEST_FRAMES):
            ret, frame = cap.read()
            if ret:
                ok_count += 1
        elapsed = time.perf_counter() - t0
        
        real_fps = ok_count / elapsed if elapsed > 0 else 0
        cap.release()
        
        info = f"格式={actual_cc}, 分辨率={actual_w}x{actual_h}, 报告FPS={reported_fps:.1f}"
        return real_fps, info, None
    except Exception as e:
        return None, None, str(e)

print("=" * 60)
print("摄像头诊断工具")
print("=" * 60)
print(f"设备编号: {device}")
print(f"测试分辨率: {WIDTH}x{HEIGHT}")
print(f"测试帧数: {TEST_FRAMES}")
print(f"系统: {platform.system()}")
print(f"OpenCV: {cv2.__version__}")
print()

# 检查 USB 速度 (Linux)
if platform.system() == "Linux":
    import subprocess
    try:
        result = subprocess.run(["lsusb", "-t"], capture_output=True, text=True)
        print("[USB 设备树]")
        print(result.stdout)
    except Exception:
        pass

# 测试各种格式
formats_to_test = [
    (cv2.VideoWriter_fourcc('M','J','P','G'), "MJPG"),
    (cv2.VideoWriter_fourcc('Y','U','Y','2'), "YUY2"),
    (None, "默认(不设FOURCC)"),
]

backends = []
if platform.system() == "Windows":
    backends = [
        (cv2.CAP_DSHOW, "DirectShow"),
        (cv2.CAP_MSMF, "MSMF"),
    ]
else:
    backends = [
        (None, "默认"),
        (cv2.CAP_V4L2, "V4L2"),
    ]

print(f"测试 {len(formats_to_test)} 种格式 x {len(backends)} 种后端...")
print()

best_fps = 0
best_config = ""

for backend, backend_name in backends:
    for fourcc_code, fmt_name in formats_to_test:
        label = f"{backend_name} + {fmt_name}"
        print(f"测试: {label} ...", end=" ", flush=True)
        
        real_fps, info, err = test_format(device, fourcc_code, label, backend)
        
        if err:
            print(f"失败 ({err})")
        else:
            marker = ""
            if real_fps > best_fps:
                best_fps = real_fps
                best_config = label
                marker = " <-- 最快"
            print(f"实际 {real_fps:.1f} fps  ({info}){marker}")
        
        time.sleep(0.3)

print()
print("=" * 60)
print(f"最佳配置: {best_config} ({best_fps:.1f} fps)")
if best_fps < 20:
    print()
    print("所有配置均低于 20fps，可能原因:")
    print("  1. 摄像头硬件不支持 MJPG (内部只有 YUY2)")
    print("  2. USB 实际运行在 2.0 模式 (检查线缆和接口)")
    print("  3. 摄像头驱动问题 (尝试更新驱动)")
    print()
    print("解决方案:")
    print("  A. 降低分辨率到 640x480 (YUY2 低分辨率可达 30fps)")
    print("  B. 更换支持 MJPG 的摄像头")
elif best_fps >= 20 and "MJPG" in best_config:
    print("摄像头支持 MJPG! 软件应能自动使用。如仍有问题请反馈此结果。")
print("=" * 60)
