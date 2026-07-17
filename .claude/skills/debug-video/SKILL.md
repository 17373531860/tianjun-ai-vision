---
name: debug-video
description: "诊断视频采集和推流问题：6种视频源连接失败、MJPEG推流卡顿、FFmpeg录像损坏、帧率异常、双缓冲显示问题。当画面黑屏、卡顿或录像无法播放时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__context7, mcp__sentry"
---

# debug-video: 视频采集与推流诊断

你正在诊断天军AI视觉检测系统的 **视频采集、推流和录制管线**。

用户问题: $ARGUMENTS

## 6种视频源架构

### 1. USB摄像头 (camera)
- **启动:** `VideoSourceManager.start_camera(device_index, width, height, fps)`
- **OpenCV:** `cv2.VideoCapture(device_index)`
- **前端配置:** Source页 → cameraSettings → `api.post('/source/camera/start')`
- **后端选择策略（3层回退）:**
  1. DirectShow + MJPG（标准方式）
  2. 如果 DirectShow 拿不到 MJPG → 实测 DirectShow vs MSMF 帧率，自动选快的
  3. Linux: V4L2 重试
- **常见问题:** 部分 USB 摄像头通过 DirectShow 只能拿到 YUY2（未压缩），导致 1280x720 只有 ~10fps。MSMF 通常能正确协商 MJPG 达到 30fps

### 2. 海康工业相机 (hikvision)
- **启动:** `VideoSourceManager.start_hikvision(serial_number)`
- **SDK:** `backend/api/MvImport/MvCameraControl_class.py`
- **枚举:** `get_hikvision_device_list()` 通过 MvCamera SDK

### 3. RTSP流 (rtsp)
- **启动:** `VideoSourceManager.start_rtsp(url)` — **被 hotfix.py 猴子补丁替换！**
- **实际执行:** `hotfix.py:_patch_start_rtsp()` 的替换版本
- **特性:** TCP传输、3次重试、H.265支持、帧验证
- **OpenCV:** `cv2.VideoCapture(url)` + FFmpeg 环境变量

### 4. HCNetSDK/NVR (hcnetsdk)
- **启动:** `VideoSourceManager.start_hcnetsdk(ip, port, user, password, channel)`
- **SDK:** `backend/hcnetsdk/wrapper.py` → `HCNetSession`
- **流程:** login → start_preview → 回调解码YUV→BGR

### 5. 视频文件 (video)
- **启动:** `VideoSourceManager.start_video(file_path, speed, sync_mode)`
- **OpenCV:** `cv2.VideoCapture(file_path)`
- **同步模式:** frame-by-frame（逐帧）或实时播放
- **前端上传:** `POST /source/video/upload` → `uploads/videos/`

### 6. 图片 (image)
- **启动:** `VideoSourceManager.start_image(file_path)`
- **特性:** 单帧输入，适合调试

## 采集-推流-录制管线

```
视频源 (camera/rtsp/hcnetsdk/video/image)
  ↓ start_xxx() 启动
  ↓
_capture_loop() [独立线程]
  ├── 读帧 → self.current_frame (numpy array)
  ├── 如果 is_detecting:
  │   ├── YOLO 推理
  │   ├── 绘制检测框
  │   └── 步骤状态机处理
  ├── 如果 MediaPipe 开启:
  │   └── _apply_mediapipe_overlay()
  ├── 如果正在录制:
  │   └── FFmpegRecorder.write_frame(frame)
  └── self.current_frame = 处理后的帧
  
MJPEG 推流 [主线程, HTTP请求驱动]
  main.py: GET /video_feed?channel=N
    → get_video_feed() generator
      → 读取 current_frame → cv2.imencode('.jpg') → yield
```

## MJPEG 双缓冲显示 (前端)

**单工位模式** (`Monitor/index.vue`):
```
<img> 元素A 加载 MJPEG 流
<img> 元素B 隐藏待命
切换时: B.src = streamUrl → B加载成功 → 显示B隐藏A → A.src='' 释放解码器
```
目的: 防止长时间MJPEG流导致浏览器内存泄漏

**多工位模式** (`Monitor/index.vue`):
```
fetch(streamUrl) → ReadableStream
  → 手动解析 MJPEG boundary
  → findBytes() 搜索 JPEG SOI/EOI 标记
  → Blob → createObjectURL → <img>.src
```

## FFmpeg 录像 (FFmpegRecorder)

```python
class FFmpegRecorder:
    # 通过管道将原始帧写入FFmpeg子进程
    ffmpeg -y -f rawvideo -pix_fmt bgr24 -s WxH -r FPS -i pipe:
           -c:v libx264 -preset fast -crf 23 output.mp4
```

- **路径解析:** `get_ffmpeg_path()` / `get_cached_ffmpeg_path()`
  - 开发环境: 系统 PATH 中的 ffmpeg
  - 生产环境: `resources/ffmpeg/ffmpeg.exe`
- **录像文件:** `recordings/` 目录下，按日期+时间命名

## 诊断步骤

### 黑屏/无画面
1. 确认视频源类型，检查对应 `start_xxx()` 是否成功
2. 检查 `self.is_streaming` 状态
3. 检查 `self.current_frame` 是否为 None
4. RTSP: 检查 `hotfix.py` 是否正确替换了 `start_rtsp`
5. HCNetSDK: 检查 `HCNetSession.login()` 是否成功

### 画面卡顿 / 帧率低
1. 检查采集FPS vs 显示FPS（`device_config.json` 的 `target_stream_fps`）
2. MJPEG编码开销: `cv2.imencode` 的质量参数
3. 网络带宽（RTSP/HCNetSDK）
4. 前端轮询间隔（200ms/300ms）是否与帧率匹配
5. Electron GPU内存限制: `--max-old-space-size=512`

### USB摄像头帧率只有 ~10fps
1. 检查后端日志 `[Camera] Capture format:` 行，确认是 MJPG 还是 YUY2
2. YUY2 @ 1280x720 = ~1.8MB/帧，USB 2.0 带宽不足以 30fps
3. 确认 Strategy 2（MSMF 回退）是否执行：日志应有 `DirectShow(YUY2) 实测 Xfps` 和 `MSMF(MJPG) 实测 Xfps`
4. 如果 MSMF 也拿不到 MJPG → 摄像头硬件不支持 MJPG，只能降分辨率到 640x480
5. 远程诊断: 访问 `GET /api/v1/source/status`，检查 `fps_actual` 字段
6. 快速测试脚本: `test_camera_backend.py`（分别测 DirectShow 和 MSMF 实际帧率）

### 录像无法播放
1. 检查 FFmpeg 路径是否正确
2. 检查 `convert_video_for_browser()` (sessions.py) 转码是否成功
3. 原始录像格式可能不是H.264，需要转码
4. 文件大小为0 → FFmpegRecorder 管道断开

### 多工位视频串流
1. 每个通道是独立的 VideoSourceManager 实例（`channel_manager.py`）
2. `GET /video_feed?channel=N` 需要正确传递 channel 参数
3. 前端 ReadableStream 解析可能丢帧（`findBytes` 是逐字节搜索）

## 关键文件
- `backend/api/source.py` — VideoSourceManager: start_xxx(), _capture_loop(), FFmpegRecorder
- `backend/hotfix.py` — start_rtsp 替换版本
- `backend/hcnetsdk/wrapper.py` — HCNetSDK 封装
- `backend/api/sessions.py` — convert_video_for_browser(), 视频播放端点
- `backend/main.py` — /video_feed 和 /snapshot 端点
- `backend/data/device_config.json` — 帧率和设备配置
- `frontend/src/views/Monitor/index.vue` — MJPEG显示和双缓冲逻辑
- `frontend/src/views/Source/index.vue` — 视频源配置和启动

### OpenCV/NumPy ABI 不兼容
1. 症状: `cv2.putText` / `cv2.resize` 报 `img is not a numpy array`，推理完全不工作
2. 原因有两层:
   - **版本层:** `opencv-contrib-python>=4.11` 与 `numpy<2.0` 的 C API 不兼容
   - **编译层:** conda-pack 环境中的 numpy 是 conda 编译的（MKL），pip 的 opencv 是 OpenBLAS 编译的，即使版本匹配 ABI 也不兼容
3. 验证: `python -c "import numpy as np, cv2; img=np.zeros((100,100,3),dtype=np.uint8); cv2.putText(img,'OK',(10,50),cv2.FONT_HERSHEY_SIMPLEX,1,(255,255,255),2); print('OK')"`
4. **客户端修复流程（关键：必须物理删除 + 重装）:**
   ```
   pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python opencv-contrib-python-headless numpy
   rd /s /q site-packages\cv2
   rd /s /q site-packages\numpy
   rd /s /q site-packages\numpy.libs
   # 清除所有 dist-info 残留
   pip install numpy==1.26.4 --no-cache-dir
   pip install opencv-contrib-python==4.10.0.84 --no-cache-dir
   ```
   单纯 `pip install --force-reinstall` 可能不够，必须物理删除 numpy 和 cv2 目录
5. 预防: CI 中 `pip install --force-reinstall numpy`，限制 `opencv-contrib-python<4.11`

### 客户端一键补丁部署
当客户端出现上述问题时:
1. 准备 `patch_v2.2.1a.bat` + `hotfix.py` 两个文件
2. 将两个文件放入同一目录，右键以管理员身份运行
3. 输入安装路径（脚本会自动检测常见路径）
4. 脚本自动: 关闭软件 → pip卸载+物理删除numpy/cv2 → 清pycache → pip全新安装 → 验证 → 部署hotfix
5. **注意: bat 脚本必须纯 ASCII，不能有中文**（cmd.exe 用 GBK 解析会破坏语法）

## 已知陷阱
- `get_ffmpeg_path()` 在 source.py 和 sessions.py 重复定义
- HCNetSDK `_on_decode` 回调可能在 `stop_preview` 后仍被调用（竞态）
- MJPEG `/video_feed` 是长连接，Vite代理可能阻塞其他API请求（前端直连 localhost:8001 绕过）
- `electron/main.js` 开发模式连 localhost:5173，但 vite 实际在 6001（端口不匹配）
- USB摄像头 DirectShow vs MSMF 后端选择对帧率影响极大（可差 3 倍），部署前用 `test_camera_backend.py` 验证
- `_reopen_camera()`（捕获线程重连）和 `_reopen_camera()`（resume）也需设置 MJPG fourcc，否则重连后可能退回 YUY2
- **相机重开必须回放曝光设置**（v3.41.1 技彩锁帧修复）：pause/resume、采集线程断线重连、前端 localStorage 恢复三条重开路径都要重放 `_apply_exposure_setting`，否则自动曝光复活压死帧率（现场表现"画面像卡死"）。曝光写入按 backend 语义精准下发（MSMF AE=0 / DSHOW AE=0.25，别学老代码两个都写），resume 沿用启动时存的 backend id 而非硬编码 DSHOW；每次 set 有 `[Camera/Exposure]` 三元日志（请求值/set 返回/回读值），排查"曝光设了没生效"先看这组日志判断是驱动拒绝还是量化
- **Windows MSMF 硬件变换必须关**（v3.41.1，`main.py` bootstrap `OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0`，必须在 cv2 import 前）：技彩 UVC 相机开 HW transforms 时 VideoCapture open 和每次 set 分辨率/FPS 要重协商数秒，现场表现"切源/重连巨慢"
- **MSMF 后端竞争**: 测试 MSMF 前必须先 `release()` DirectShow + `sleep(0.3)`，否则两个后端同时抢占摄像头，benchmark 可能侥幸通过但 `_capture_loop` 会 `can't grab frame`
- **OpenCV 4.11 ABI**: 打包环境中 `opencv-contrib-python>=4.11` 与 `numpy<2.0` 不兼容，必须限制 `<4.11`
- **conda numpy ABI**: conda-pack 后 numpy 是 conda 编译的，与 pip opencv 不兼容。CI 必须 `--force-reinstall` numpy。客户端修复必须物理删除再重装
- 实际采集帧率比 benchmark 低 ~30%（benchmark 只做 `cap.read()`，实际还有 MJPEG 编码 + 帧拷贝 + 线程同步 ~15ms/帧）