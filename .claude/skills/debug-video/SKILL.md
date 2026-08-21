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
- **枚举（v3.51.3 起）:** `_detect_cameras_windows`（source_routes.py）Windows 上优先用打包内 ffmpeg `-list_devices -f dshow` 列设备——快、带设备真名、不试开；再按 USB 设备路径解析出的 `(vid, pid, serial)` 去重同一物理机的重复 DirectShow filter（复合设备 IR 副摄/驱动重复注册会让一台机器占两个 index；两台同型号相机 serial 不同不会误合并）。ffmpeg 不可用/解析失败/非 Windows 自动回退老的逐 index 试开法。"使用中"标记看全部工位（`_camera_indexes_in_use`），不再只看 ch0
- **跨工位抢相机（v3.51.3 起）:** `_start_camera_locked` 打开前先做跨通道占用预检（`_find_camera_index_conflict`），别的工位正持有同一 device_index 时毫秒级抛"摄像头 N 正在被工位 X 使用"，不进 3 轮 DSHOW/MSMF 重试循环；预检挡在 `self.stop()` 之前，不会误停本工位旧源
- **诊断"枚举重复/双工位同选超时":** 列表出现同名或连续可开的可疑 index → 查后端日志 `[Camera] 枚举去重` / `[Camera] ffmpeg 列设备失败, 回退试开法枚举` 看走的是 ffmpeg 路径还是回退试开路径；回退路径无法物理去重是已知限制（ffmpeg 缺失才会发生）

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
- **目标帧率 (v3.45 起可配):** Source 页表单 10/15/25/30/50/60 下拉（此前硬编码 25），持久化键 `hcnet_fps`，开机自动恢复按持久化值回放（`backend/main.py: auto_restore_video_sources`）。排查"重启后帧率跌回 25" → 看持久化配置里有没有该键（老配置无键默认 25）。超过设备码流实际帧率不会增加画面帧数

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
class FFmpegRecorder:  # backend/api/source_recorder.py
    # 通过管道将原始帧写入FFmpeg子进程
    ffmpeg -y -f rawvideo -pix_fmt bgr24 -s WxH -r FPS -i pipe:
           -c:v libx264 -preset ultrafast -crf 28 -g 125
           -movflags +frag_keyframe+empty_moov+default_base_moof
           -flush_packets 1 output.mp4
```

- **v3.54 长录像治理（三层，治"24h 录像视频加载失败"）:**
  1. **录制中写 fragmented MP4**: 每 5s 一个 fragment 即时落盘（`-g 125` @25fps
     + `-flush_packets 1`），文件任意时刻可播；断电/强杀最多丢 ~5s。此前
     `+faststart` 收尾要整文件重写 moov，`release()` 3s 超时就 kill，大文件必坏
  2. **收尾异步 remux**: `release()` 后守护线程跑 `remux_to_faststart()`
     （`-c copy` 流拷贝, tmp+原子替换, 磁盘余量护栏），整理成 moov 前置的常规
     mp4（时长精确/秒开/老播放器兼容）；remux 失败/中断保留 fMP4 照样能播。
     日志 `[FFmpeg录制] 收尾整理完成(faststart)`
  3. **回放按需转码**: `sessions.py _is_browser_compatible_h264()` 用
     `ffmpeg -i` stderr 探测（不依赖 ffprobe），h264+yuv420p 直接原文件出流
     （Starlette FileResponse 支持 Range 拖动），只有老 mp4v 等才走
     `convert_video_for_browser` 转码。排查"还在转码"先看探测日志
- **v3.54 会话录像自动分段**: 会话级录像每 `TJ_SESSION_SEGMENT_SECONDS`
  （默认 3600s）自动换段续录，`source_recording_api_mixin.py`
  `_rotate_session_recording()`（录制线程内触发，开新段失败保老段 60s 重试，
  绝不断流）。每段独立 VideoClip（clip_type='session', related_id=session_id），
  `session.video_id/video_path` 恒指首段；分段列表
  `GET /data/sessions/{id}/videos`，前端播放弹窗多段时显示切换条
  （`VideoPlayerDialog.openSegments`）。周期/步骤录像不分段。
  排查"没分段"：确认是会话录像（周期录像本来就不分）+ 看
  `[Recording] session video rotated -> segment N` 日志。
  测试加速：起后端时 `TJ_SESSION_SEGMENT_SECONDS=30`；
  单测 `tests/test_session_segmentation.py` + `tests/test_recorder_fmp4.py`，
  e2e `tests/e2e_browser/test_session_segments.py`，
  UAT `tests/uat/uat_session_segments.py`（支持 UAT_RUN_SECONDS 长跑）

- **路径解析:** `get_ffmpeg_path()` / `get_cached_ffmpeg_path()`
  - 开发环境: 系统 PATH 中的 ffmpeg
  - 生产环境: `resources/ffmpeg/ffmpeg.exe`
- **录像文件:** 默认 `recordings/` 目录下，按日期+时间命名
- **自定义录像存储位置 (v3.54):** `backend/services/recording_storage.py` —
  SystemConfig KV `recording_storage_dir` 存自定义根目录（空=默认），三个开录点
  （`source_recording_api_mixin.py` 的 session/cycle/step）每次开录动态解析
  `get_video_dirs()`，实时生效；转码缓存/清理扫描/存储统计跟随
  （`sessions_maintenance._all_video_scan_dirs()`）。排查"录像没写进指定盘"先
  `GET /api/v1/data/storage/recording-dir` 看 `effective_root`，自定义目录不可用
  时会打日志 `[RecordingStorage]` 并回退默认（绝不丢录像）。回放与目录无关
  （DB 存绝对路径）。

## 诊断步骤

### 黑屏/无画面
1. 确认视频源类型，检查对应 `start_xxx()` 是否成功
2. 检查 `self.is_streaming` 状态
3. 检查 `self.current_frame` 是否为 None
4. RTSP: 检查 `hotfix.py` 是否正确替换了 `start_rtsp`
5. HCNetSDK: 检查 `HCNetSession.login()` 是否成功
6. **启动"成功"但永远 No Source 的僵尸态**（v3.51.2 BUG-001）：接口 200、
   `is_running=True`、心跳照跳，但快照恒 ~10KB 占位图 → 开
   `backend.capture` 调试 flag 看采集摘要——若"采集=0.0fps 读帧均耗=0.0ms"
   说明句柄是死的（`isOpened()=False` 但 `capture is not None`，采集循环
   `read()` 立即 False 空转）。历史成因：格式探测 Strategy 3 在 macOS 误入
   V4L2 重开（守门已收紧仅 Linux）；v3.51.2 起启动尾部有终检，死句柄会
   显式抛"打开后句柄失效"而不是静默僵尸。真机回归剧本
   `tests/uat/mac_camera_sim/`。注意心跳日志的 `FPS=` 是 `fps_actual`
   旧值残留（只在成功读帧时刷新），不能当读帧活着的证据

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
2. v3.54 起自录 h264 直出不转码；仅老 mp4v 走 `convert_video_for_browser()`
   (sessions.py) 转码，检查转码是否成功
3. 原始录像格式可能不是H.264，需要转码
4. 文件大小为0 → FFmpegRecorder 管道断开
5. v3.54 前的历史坑（已治）: 超长录像收尾 moov 重写被 3s 超时 kill 整段废 +
   回放无条件重转码 300s 超时回退坏原片 → 现象"视频加载失败"。v3.54 起
   录制 fMP4 断电可播 + 收尾异步 remux + h264 直出，若仍复现先确认客户版本

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
## v3.51.5 补充：枚举回退留痕 + 开机恢复模型直载/用户否决

1. **ffmpeg 枚举回退不再静默**：`_list_dshow_devices_ffmpeg` 解析为空时打印 stderr 头部 8 行；`_detect_cameras_windows` 走老试开法兜底时明示（设备名"摄像头 N"式=在走兜底，搜 `[Camera]` 前缀即知走的哪条路）。
2. **启动直载转换引擎**：`main.py` `_resolve_startup_model_path` 按 `project.model_format` 解析 ModelConversion 表的转换引擎（GPU 架构匹配才用），`_load_channel_model_with_fallback` 引擎失败回退 `.pt`——治启动先载 .pt 再重载 TRT 的双重加载慢启动。
3. **自动恢复用户否决**：`_restore_detection_pass` 记录自动拉起通道的 capture 线程 id，下轮检测停了而线程未变=用户手动停，记 `user_vetoed` 不再拉起；线程换了（源真重启）照常恢复。单测 `tests/test_boot_restore_v3515.py`。
