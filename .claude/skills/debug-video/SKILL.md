---
name: debug-video
description: "诊断视频采集和推流问题：6种视频源连接失败、MJPEG推流卡顿、FFmpeg录像损坏、帧率异常、双缓冲显示问题。当画面黑屏、卡顿或录像无法播放时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
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

## 已知陷阱
- `get_ffmpeg_path()` 在 source.py 和 sessions.py 重复定义
- HCNetSDK `_on_decode` 回调可能在 `stop_preview` 后仍被调用（竞态）
- MJPEG `/video_feed` 是长连接，Vite代理可能阻塞其他API请求（前端直连 localhost:8001 绕过）
- `electron/main.js` 开发模式连 localhost:5173，但 vite 实际在 6001（端口不匹配）
- USB摄像头 DirectShow vs MSMF 后端选择对帧率影响极大（可差 3 倍），部署前用 `test_camera_backend.py` 验证
- `_reopen_camera()`（捕获线程重连）和 `_reopen_camera()`（resume）也需设置 MJPG fourcc，否则重连后可能退回 YUY2
