---
name: add-source-type
description: "新增视频源类型的完整流程：source.py接入方法、Source页UI、sourceStore持久化、Monitor显示适配。当需要支持新的摄像头或输入源时使用。"
argument-hint: "[新视频源类型描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, Edit, Write"
---

# add-source-type: 新增视频源类型

你正在帮用户为天军AI视觉检测系统添加新的视频源类型。

需求: $ARGUMENTS

## 现有6种视频源的实现模式

| 类型 | sourceType | 后端start方法 | 前端API调用 |
|------|-----------|--------------|-------------|
| USB摄像头 | `camera` | `start_camera(index, w, h, fps)` | `POST /source/camera/start` |
| 海康工业相机 | `hikvision` | `start_hikvision(serial)` | `POST /source/hikvision/start` |
| RTSP | `rtsp` | `start_rtsp(url)` (被hotfix替换) | `POST /source/rtsp/start` |
| HCNetSDK | `hcnetsdk` | `start_hcnetsdk(ip,port,user,pwd,ch)` | `POST /source/hcnetsdk/start` |
| 视频文件 | `video` | `start_video(path, speed, sync)` | `POST /source/video/start` |
| 图片 | `image` | `start_image(path)` | `POST /source/image/start` |

## 新增视频源需要修改的位置（全链路）

### 1. 后端 source.py — 采集实现

**a. 添加 start_xxx() 方法:**
```python
def start_new_source(self, param1, param2, ...):
    """启动新视频源"""
    self.source_type = 'new_source'
    # 初始化连接
    # ...
    # 启动采集线程
    self._capture_thread = threading.Thread(
        target=self._capture_loop, daemon=True
    )
    self._capture_thread.start()
    self.is_streaming = True
```

**b. 在 _capture_loop() 中添加帧读取逻辑:**
搜索 `_capture_loop` 中各视频源的帧读取分支:
```python
if self.source_type == 'new_source':
    frame = self._read_new_source_frame()
```

**c. 添加 stop/release 方法（或在通用 stop 中处理）:**
搜索 `stop` 和 `release` 方法，添加新源的清理逻辑。

**d. 添加 API 端点:**
在 source_router 上添加:
```python
@router.post("/new_source/start")
def start_new_source(request: NewSourceRequest):
    mgr = get_video_manager()
    mgr.start_new_source(request.param1, ...)
    return {"status": "ok"}
```

### 2. 前端 useSourceStore — 状态持久化

**文件:** `frontend/src/store/useSourceStore.js`

```javascript
// 添加新源的配置状态
state: () => ({
    // ...现有字段...
    newSourceSettings: {
        param1: '',
        param2: 0,
    }
})
```

添加对应的 setter 和 saveConfig/loadConfig 处理。

### 3. 前端 Source/index.vue — 配置UI

**a. 源类型选择器:**
搜索 sourceType 的选项列表，添加新选项。

**b. 配置卡片:**
添加新源的配置面板（参考现有源的卡片结构）:
```vue
<el-card v-if="sourceStore.sourceType === 'new_source'">
  <!-- 新源的配置字段 -->
</el-card>
```

**c. 启动函数:**
添加连接/启动按钮的处理函数:
```javascript
async function startNewSource() {
    await api.post('/source/new_source/start', {
        param1: sourceStore.newSourceSettings.param1,
        ...
    })
}
```

### 4. 前端 Monitor/index.vue — 显示适配

通常不需要改动，因为所有视频源最终都通过 `/video_feed` MJPEG 推流。
但如果新源有特殊显示需求（如叠加信息），需要在 Monitor 中处理。

### 5. 前端 Navbar.vue — 自动恢复

如果支持自动恢复（上次使用的源自动连接），需要在 Navbar 的 auto-restore 逻辑中添加新源类型的处理:
```javascript
if (sourceType === 'new_source') {
    await api.post('/source/new_source/start', savedConfig)
}
```

### 6. 多工位支持

如果新源需要多工位支持，确认:
- Source 页多工位模式下的每通道配置
- `start_xxx` 方法支持 channel 参数
- ChannelManager 能正确创建带新源的实例

## 检查清单

- [ ] source.py: start_new_source() 方法
- [ ] source.py: _capture_loop() 帧读取分支
- [ ] source.py: stop/release 清理逻辑
- [ ] source.py: API端点 POST /source/new_source/start
- [ ] useSourceStore: 新源配置状态 + 持久化
- [ ] Source/index.vue: 源类型选项 + 配置卡片 + 启动函数
- [ ] Navbar.vue: 自动恢复逻辑（如需要）
- [ ] Monitor/index.vue: 显示适配（如需要）
- [ ] 多工位支持（如需要）

## 模式参考

参考 `start_rtsp()` 的实现最为典型:
1. 接收参数 → 2. 创建 VideoCapture → 3. 验证帧 → 4. 启动采集线程
注意 `start_rtsp` 被 hotfix 替换了，看 hotfix.py 中的版本更完整。
