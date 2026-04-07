#!/usr/bin/env python3
"""FP16 半精度推理优化 — 前后端一致性验证

验证:
1. 后端: use_half 属性初始化、持久化、三个推理方法使用
2. 后端: API 端点包含 use_half
3. 后端: 模型预热支持 half 参数
4. 后端: 摄像头格式回退警告
5. 前端: systemStore 默认值
6. 前端: Settings 页面开关
7. 前端: 保存/加载 同步 use_half
"""

import os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
passed = 0
failed = 0
errors = []

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        msg = f"  ✗ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        errors.append(name)

def read(relpath):
    with open(os.path.join(BASE, relpath), encoding='utf-8') as f:
        return f.read()

# ═══════════════════════════════════════
#  1. 后端: use_half 属性
# ═══════════════════════════════════════
print("\n[1] 后端 use_half 属性")
src = read('backend/api/source.py')

check("use_half 初始化为 False",
      "self.use_half = False" in src)

check("_load_device_config 读取 use_half",
      "self.use_half = config.get('use_half', False)" in src)

check("_save_device_config 保存 use_half",
      "'use_half': self.use_half" in src)

check("加载日志包含 FP16 信息",
      "FP16={self.use_half}" in src)

# ═══════════════════════════════════════
#  2. 后端: 三个推理方法
# ═══════════════════════════════════════
print("\n[2] 后端推理方法 — half 参数")

check("_detect_only 计算 _half 标志",
      "_half = self.use_half and device.startswith('cuda')" in src)

detect_only_section = src[src.index("def _detect_only"):][:1500]
check("_detect_only predict 传入 half=_half",
      "half=_half" in detect_only_section)

detect_track_section = src[src.index("def _detect_and_track"):][:1500]
check("_detect_and_track track 传入 half=_half",
      "half=_half" in detect_track_section)

detect_seg_section = src[src.index("def _detect_segment"):][:1500]
check("_detect_segment predict 传入 half=_half",
      "half=_half" in detect_seg_section)

check("_detect_and_track 中有 _half 计算",
      "_half = self.use_half and device.startswith('cuda')" in detect_track_section)

check("_detect_segment 中有 _half 计算",
      "_half = self.use_half and device.startswith('cuda')" in detect_seg_section)

# ═══════════════════════════════════════
#  3. 后端: 模型预热
# ═══════════════════════════════════════
print("\n[3] 后端模型预热")

warmup_section = src[src.index("CUDA warm-up"):][:300]
check("预热使用 self.use_half",
      "_half = self.use_half" in src and "half=_half" in warmup_section)

check("预热日志显示 half 状态",
      'half={_half}' in src)

# ═══════════════════════════════════════
#  4. 后端: API 端点
# ═══════════════════════════════════════
print("\n[4] 后端 API 端点")

check("StreamConfigRequest 包含 use_half 字段",
      "use_half: bool = False" in src)

check("GET /stream/config 返回 use_half",
      '"use_half": video_manager.use_half' in src)

check("POST /stream/config 设置 use_half",
      "video_manager.use_half = req.use_half" in src)

check("POST 响应包含 use_half",
      '"use_half": video_manager.use_half' in src)

# ═══════════════════════════════════════
#  5. 后端: 摄像头格式警告
# ═══════════════════════════════════════
print("\n[5] 后端摄像头格式警告")

check("非 MJPG 格式打印警告",
      "cc_str != 'MJPG'" in src and "USB 捕获帧率可能受限" in src)

# ═══════════════════════════════════════
#  6. 前端: systemStore
# ═══════════════════════════════════════
print("\n[6] 前端 systemStore")
store_src = read('frontend/src/store/useSystemStore.js')

check("halfPrecision 字段存在",
      "halfPrecision" in store_src)

check("默认值为 false",
      "halfPrecision: false" in store_src)

check("在 performance 对象内",
      store_src.index("halfPrecision") > store_src.index("performance:"))

# ═══════════════════════════════════════
#  7. 前端: Settings 页面
# ═══════════════════════════════════════
print("\n[7] 前端 Settings 页面")
settings_src = read('frontend/src/views/Settings/index.vue')

check("FP16 半精度推理 标签存在",
      "FP16 半精度推理" in settings_src)

check("推理加速 卡片标题存在",
      "推理加速" in settings_src)

check("Lightning 图标已导入",
      "Lightning } from '@element-plus/icons-vue'" in settings_src)

check("el-switch 绑定 halfPrecision",
      'v-model="store.performance.halfPrecision"' in settings_src)

check("change 事件触发 savePerformanceSettings",
      'halfPrecision" @change="savePerformanceSettings"' in settings_src)

check("说明文字包含 RTX 系列信息",
      "RTX 20/30/40/50" in settings_src)

check("说明文字提示下次加载模型生效",
      "下次加载模型时生效" in settings_src)

# ═══════════════════════════════════════
#  8. 前端: 保存/加载同步
# ═══════════════════════════════════════
print("\n[8] 前端保存/加载同步")

check("保存时发送 use_half 到后端",
      "use_half: store.performance.halfPrecision" in settings_src)

check("加载时从后端读取 use_half",
      "res.data.use_half" in settings_src)

check("加载时赋值给 halfPrecision",
      "store.performance.halfPrecision = res.data.use_half" in settings_src)

# ═══════════════════════════════════════
#  9. 安全性验证
# ═══════════════════════════════════════
print("\n[9] 安全性验证")

check("FP16 仅在 CUDA 设备上启用 (detect_only)",
      "self.use_half and device.startswith('cuda')" in detect_only_section)

check("FP16 仅在 CUDA 设备上启用 (detect_and_track)",
      "self.use_half and device.startswith('cuda')" in detect_track_section)

check("FP16 仅在 CUDA 设备上启用 (detect_segment)",
      "self.use_half and device.startswith('cuda')" in detect_seg_section)

check("默认值为 False (不改变现有行为)",
      "self.use_half = False" in src and "halfPrecision: false" in store_src)

# ═══════════════════════════════════════
#  汇总
# ═══════════════════════════════════════
total = passed + failed
print(f"\n{'='*55}")
print(f"  总计: {total} 项 | ✓ 通过: {passed} | ✗ 失败: {failed}")
print(f"{'='*55}")

if failed:
    print(f"\n失败项:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("\n全部通过! FP16 半精度推理优化前后端一致性验证完毕。")
    sys.exit(0)
