"""
RTSP 现场诊断脚本
用法:
  python rtsp_diagnose.py                          # 测试默认三个通道
  python rtsp_diagnose.py rtsp://admin:pwd@IP/xxx  # 测试指定地址
  
客户机上用打包的 Python:
  D:\tianjunkeji\tianjun-ai-vision\resources\python\python.exe rtsp_diagnose.py
"""

import sys
import time
import os

def test_network(host, port=554):
    """测试网络连通性"""
    import socket
    print(f"\n{'='*50}")
    print(f"[1/4] 网络连通性测试: {host}:{port}")
    print(f"{'='*50}")
    
    # Ping
    print(f"  Ping {host}...", end=" ")
    if sys.platform == "win32":
        ret = os.system(f"ping -n 1 -w 2000 {host} > nul 2>&1")
    else:
        ret = os.system(f"ping -c 1 -W 2 {host} > /dev/null 2>&1")
    print("✓ 通" if ret == 0 else "✗ 不通")
    
    # TCP 端口
    print(f"  TCP {host}:{port}...", end=" ")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect((host, port))
        sock.close()
        print("✓ 端口开放")
        return True
    except Exception as e:
        print(f"✗ 连接失败: {e}")
        return False


def test_ffmpeg_codecs():
    """测试 FFmpeg 编解码器支持"""
    print(f"\n{'='*50}")
    print("[2/4] FFmpeg 编解码器检查")
    print(f"{'='*50}")
    
    import cv2
    print(f"  OpenCV 版本: {cv2.__version__}")
    
    info = cv2.getBuildInformation()
    if "FFMPEG:                      YES" in info:
        print("  FFmpeg 后端: ✓ 可用")
    else:
        print("  FFmpeg 后端: ✗ 不可用 — RTSP 无法工作！")
        return False
    
    for line in info.split('\n'):
        line = line.strip()
        if line.startswith('avcodec:') or line.startswith('avformat:'):
            print(f"  {line}")
    
    # 测试 H.265 本地解码
    import tempfile, subprocess, shutil
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        print(f"  系统 ffmpeg: {ffmpeg_path}")
        try:
            result = subprocess.run([ffmpeg_path, "-decoders"], capture_output=True, text=True, timeout=5)
            has_hevc = any("hevc" in l.lower() for l in result.stdout.split('\n') if 'hevc' in l.lower())
            print(f"  HEVC 解码器: {'✓ 有' if has_hevc else '✗ 无'}")
        except:
            pass
    
    # 用 OpenCV 直接测试 H.265 文件解码
    test_file = os.path.join(tempfile.gettempdir(), "_h265_test.mp4")
    try:
        if ffmpeg_path:
            subprocess.run([
                ffmpeg_path, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
                "-c:v", "libx265", "-preset", "ultrafast", "-loglevel", "quiet", test_file
            ], timeout=10, capture_output=True)
            
            cap = cv2.VideoCapture(test_file)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                print(f"  H.265 本地解码: {'✓ 成功' if ret else '✗ 失败'}")
            else:
                print("  H.265 本地解码: ✗ 无法打开测试文件")
            os.remove(test_file)
        else:
            print("  H.265 本地解码: 跳过（无 ffmpeg 生成测试文件）")
    except Exception as e:
        print(f"  H.265 本地解码: 跳过 ({e})")
    
    return True


def test_rtsp_stream(url, label=""):
    """测试单个 RTSP 流"""
    import cv2
    
    tag = f" [{label}]" if label else ""
    print(f"\n{'='*50}")
    print(f"[3/4] RTSP 流测试{tag}")
    print(f"{'='*50}")
    
    safe_url = url
    if "@" in url:
        parts = url.split("@")
        prefix = parts[0].split("//")[0] + "//***:***@"
        safe_url = prefix + parts[1]
    print(f"  地址: {safe_url}")
    
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
    
    # 连接
    print("  连接中...", end=" ", flush=True)
    t0 = time.time()
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    conn_time = time.time() - t0
    
    if not cap.isOpened():
        print(f"✗ 连接失败 ({conn_time:.1f}s)")
        print("  → 检查: URL是否正确 / 用户名密码 / NVR是否在线 / 554端口")
        return False
    
    print(f"✓ 已连接 ({conn_time:.1f}s)")
    
    # 流信息
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    codec = ''.join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)]) if fourcc else "未知"
    
    print(f"  分辨率: {w}x{h}")
    print(f"  帧率: {fps}")
    print(f"  编码: {codec}")
    
    if "hevc" in codec.lower() or "h265" in codec.lower():
        print("  ⚠ 使用 H.265 编码，如果读帧失败建议改为 H.264")
    elif "h264" in codec.lower() or "avc" in codec.lower():
        print("  ✓ H.264 编码，兼容性最好")
    
    # 试读帧（最多等 10 秒）
    print("  读帧测试...", end=" ", flush=True)
    success = False
    frames = 0
    t0 = time.time()
    
    for attempt in range(50):
        ret, frame = cap.read()
        if ret:
            frames += 1
            if frames >= 5:
                success = True
                break
        else:
            time.sleep(0.2)
    
    read_time = time.time() - t0
    
    if success:
        print(f"✓ 成功读取 {frames} 帧 ({read_time:.1f}s), 帧尺寸: {frame.shape}")
    else:
        print(f"✗ 读取失败 (尝试 {read_time:.1f}s, 仅获得 {frames} 帧)")
        print("  → 可能原因: 编码格式不支持 / 流异常")
        print("  → 建议: 在 NVR 管理页面将通道编码改为 H.264")
        cap.release()
        return False
    
    # 性能测试（读 50 帧测速）
    print("  性能测试 (50帧)...", end=" ", flush=True)
    t0 = time.time()
    count = 0
    for _ in range(50):
        ret, frame = cap.read()
        if ret:
            count += 1
    elapsed = time.time() - t0
    actual_fps = count / elapsed if elapsed > 0 else 0
    
    print(f"✓ {actual_fps:.1f} FPS (解码速度)")
    
    cap.release()
    
    print(f"\n  结论: {'✓ 该通道可正常使用' if success else '✗ 该通道存在问题'}")
    return success


def test_multi_stream(urls):
    """测试多路同时解码性能"""
    import cv2
    
    if len(urls) < 2:
        return
    
    print(f"\n{'='*50}")
    print(f"[4/4] 多路并行性能测试 ({len(urls)} 路)")
    print(f"{'='*50}")
    
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
    
    caps = []
    for url in urls:
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if cap.isOpened():
            caps.append(cap)
        else:
            safe = url.split("@")[1] if "@" in url else url
            print(f"  ✗ 无法连接: ...@{safe}")
    
    if len(caps) < 2:
        print("  连接不足 2 路，跳过并行测试")
        for c in caps:
            c.release()
        return
    
    print(f"  已连接 {len(caps)} 路，测试 100 帧轮询...")
    
    t0 = time.time()
    total_frames = 0
    per_stream = [0] * len(caps)
    
    for _ in range(100):
        for i, cap in enumerate(caps):
            ret, _ = cap.read()
            if ret:
                total_frames += 1
                per_stream[i] += 1
    
    elapsed = time.time() - t0
    
    for i, count in enumerate(per_stream):
        fps = count / elapsed if elapsed > 0 else 0
        print(f"  通道 {i+1}: {count} 帧, {fps:.1f} FPS")
    
    total_fps = total_frames / elapsed if elapsed > 0 else 0
    print(f"  总计: {total_frames} 帧, {total_fps:.1f} FPS (合计吞吐)")
    print(f"  平均每路: {total_fps / len(caps):.1f} FPS")
    
    for cap in caps:
        cap.release()


def main():
    print("=" * 50)
    print("  天军科技 RTSP 现场诊断工具")
    print("=" * 50)
    
    NVR_HOST = "192.168.1.168"
    NVR_USER = "admin"
    NVR_PASS = "SJYFsjyf789"
    
    DEFAULT_CHANNELS = {
        "D8 子码流":  f"rtsp://{NVR_USER}:{NVR_PASS}@{NVR_HOST}:554/Streaming/Channels/802",
        "D13 子码流": f"rtsp://{NVR_USER}:{NVR_PASS}@{NVR_HOST}:554/Streaming/Channels/1302",
        "D14 子码流": f"rtsp://{NVR_USER}:{NVR_PASS}@{NVR_HOST}:554/Streaming/Channels/1402",
    }
    
    if len(sys.argv) > 1:
        url = sys.argv[1]
        test_ffmpeg_codecs()
        test_rtsp_stream(url, "自定义地址")
        return
    
    # 完整诊断流程
    print(f"\nNVR: {NVR_HOST}")
    print(f"通道: {', '.join(DEFAULT_CHANNELS.keys())}")
    
    # Step 1: 网络
    if not test_network(NVR_HOST, 554):
        print("\n✗ 网络不通，请检查:")
        print("  1. 网线是否接在 NVR 的 LAN 口")
        print("  2. 本机 IP 是否设置为 192.168.1.x 网段")
        print("  3. 防火墙是否放行 554 端口")
        return
    
    # Step 2: FFmpeg
    test_ffmpeg_codecs()
    
    # Step 3: 逐个测试通道
    working_urls = []
    for label, url in DEFAULT_CHANNELS.items():
        ok = test_rtsp_stream(url, label)
        if ok:
            working_urls.append(url)
    
    # Step 4: 多路并行
    if len(working_urls) >= 2:
        test_multi_stream(working_urls)
    
    # 总结
    print(f"\n{'='*50}")
    print("  诊断总结")
    print(f"{'='*50}")
    print(f"  可用通道: {len(working_urls)}/{len(DEFAULT_CHANNELS)}")
    
    if len(working_urls) == len(DEFAULT_CHANNELS):
        print("  ✓ 所有通道正常，可以在软件中填入 RTSP 地址使用")
    elif working_urls:
        print("  ⚠ 部分通道有问题，请检查失败通道的编码设置")
    else:
        print("  ✗ 所有通道均失败，请检查 NVR 配置")
    
    print(f"\n要填入软件的地址:")
    for label, url in DEFAULT_CHANNELS.items():
        safe = url.replace(NVR_PASS, "****")
        print(f"  {label}: {safe}")


if __name__ == "__main__":
    main()
