"""
摄像头后端测试脚本 - 快速诊断 DirectShow vs MSMF
用法: python test_camera_backend.py [摄像头编号，默认0]

客户端运行方式:
  D:\tianjun\tianjun-ai-vision\resources\python\python.exe test_camera_backend.py
"""
import cv2, time, sys

device = int(sys.argv[1]) if len(sys.argv) > 1 else 0
WIDTH, HEIGHT, FPS = 1280, 720, 30
MJPG = cv2.VideoWriter_fourcc('M','J','P','G')

def fourcc_str(cap):
    fc = int(cap.get(cv2.CAP_PROP_FOURCC))
    return "".join([chr((fc >> (8*i)) & 0xFF) for i in range(4)])

def bench(cap, n=30):
    for _ in range(5):
        cap.read()
    t0 = time.perf_counter()
    ok = 0
    for _ in range(n):
        ret, _ = cap.read()
        if ret: ok += 1
    elapsed = time.perf_counter() - t0
    return ok / elapsed if elapsed > 0 else 0

def test_backend(name, backend_flag):
    print(f"\n{'='*40}")
    print(f"  测试: {name}")
    print(f"{'='*40}")
    cap = cv2.VideoCapture(device, backend_flag)
    if not cap.isOpened():
        print(f"  ✗ 无法打开摄像头")
        return
    cap.set(cv2.CAP_PROP_FOURCC, MJPG)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)

    cc = fourcc_str(cap)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    f = cap.get(cv2.CAP_PROP_FPS)
    print(f"  格式: {cc}")
    print(f"  分辨率: {w}x{h}")
    print(f"  报告帧率: {f}")

    real_fps = bench(cap)
    print(f"  实测帧率: {real_fps:.1f} fps")

    if cc == 'MJPG' and real_fps >= 20:
        print(f"  ✓ 正常! MJPG + {real_fps:.0f}fps")
    elif real_fps >= 20:
        print(f"  △ 帧率正常但格式是 {cc}(未压缩)")
    else:
        print(f"  ✗ 帧率过低 ({real_fps:.0f}fps)")

    cap.release()

print(f"OpenCV 版本: {cv2.__version__}")
print(f"测试设备: 摄像头 #{device}, 目标: {WIDTH}x{HEIGHT}@{FPS}fps MJPG")

test_backend("DirectShow", cv2.CAP_DSHOW)
test_backend("MSMF (Media Foundation)", cv2.CAP_MSMF)

print(f"\n{'='*40}")
print("  完成! 请将以上输出发给开发人员")
print(f"{'='*40}")
