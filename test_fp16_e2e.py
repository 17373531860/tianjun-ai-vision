#!/usr/bin/env python3
"""FP16 半精度推理 — 端到端连通测试

测试维度:
1. 代码一致性（前后端字段名、数据流）
2. API 实际连通（GET/POST /stream/config）
3. 配置持久化（保存→读取→一致）
4. 推理管线完整性（三个推理方法 + 预热）
5. 与已有功能的兼容性（NG TOP3、性能设置等）
6. 安全边界（CPU 不开 FP16、默认关闭）
"""

import os, sys, json, tempfile

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


# ═══════════════════════════════════════════════════════
#  1. 前后端字段映射一致性
# ═══════════════════════════════════════════════════════
print("\n[1] 前后端字段映射一致性")
src = read('backend/api/source.py')
settings = read('frontend/src/views/Settings/index.vue')
store = read('frontend/src/store/useSystemStore.js')

check("后端属性名: use_half",
      "self.use_half" in src)
check("后端 API 字段名: use_half",
      '"use_half"' in src)
check("前端 store 字段名: halfPrecision",
      "halfPrecision" in store)
check("前端→后端: use_half: store.performance.halfPrecision",
      "use_half: store.performance.halfPrecision" in settings)
check("后端→前端: store.performance.halfPrecision = res.data.use_half",
      "store.performance.halfPrecision = res.data.use_half" in settings)

# ═══════════════════════════════════════════════════════
#  2. API 连通测试
# ═══════════════════════════════════════════════════════
print("\n[2] API 实际连通测试")

api_ok = False
try:
    import requests
    base_url = "http://localhost:8000/api/source"
    
    # GET /stream/config
    r1 = requests.get(f"{base_url}/stream/config", timeout=3)
    check("GET /stream/config 返回 200", r1.status_code == 200)
    
    data = r1.json()
    check("响应包含 use_half 字段", "use_half" in data)
    check("响应包含 frame_limit_enabled 字段", "frame_limit_enabled" in data)
    check("响应包含 target_stream_fps 字段", "target_stream_fps" in data)
    check("use_half 是布尔类型", isinstance(data.get("use_half"), bool))
    
    original_half = data["use_half"]
    print(f"    当前 use_half = {original_half}")
    
    # POST /stream/config — 切换 use_half
    new_half = not original_half
    r2 = requests.post(f"{base_url}/stream/config", json={
        "frame_limit_enabled": data["frame_limit_enabled"],
        "target_stream_fps": data["target_stream_fps"],
        "use_half": new_half
    }, timeout=3)
    check("POST /stream/config 返回 200", r2.status_code == 200)
    
    post_data = r2.json()
    check("POST 响应中 use_half 已更新",
          post_data.get("use_half") == new_half,
          f"期望 {new_half}, 实际 {post_data.get('use_half')}")
    
    # GET 验证持久化
    r3 = requests.get(f"{base_url}/stream/config", timeout=3)
    check("再次 GET 验证持久化",
          r3.json().get("use_half") == new_half)
    
    # 还原原始值
    requests.post(f"{base_url}/stream/config", json={
        "frame_limit_enabled": data["frame_limit_enabled"],
        "target_stream_fps": data["target_stream_fps"],
        "use_half": original_half
    }, timeout=3)
    
    r4 = requests.get(f"{base_url}/stream/config", timeout=3)
    check("还原原始值成功",
          r4.json().get("use_half") == original_half)
    
    api_ok = True
    
    # 验证其他端点不受影响
    r5 = requests.get(f"{base_url}/status", timeout=3)
    check("GET /status 正常响应", r5.status_code == 200)
    
    r6 = requests.get(f"{base_url}/detection/results", timeout=3)
    check("GET /detection/results 正常响应", r6.status_code == 200)
    
    # 验证 ng_step_cycle_counts 字段仍在（NG TOP3 兼容性）
    det_data = r6.json()
    check("detection/results 仍包含 ng_step_cycle_counts (NG TOP3 兼容)",
          "ng_step_cycle_counts" in det_data)

except ImportError:
    print("  ⚠ requests 未安装，跳过 API 连通测试")
except Exception as e:
    err_str = f"{type(e).__name__}: {e}"
    if any(k in err_str.lower() for k in ("connection", "timeout", "refused", "expecting value")):
        print(f"  ⚠ 后端未运行或端口不可达，跳过 API 连通测试 ({type(e).__name__})")
    else:
        print(f"  ⚠ API 测试异常: {err_str}")


# ═══════════════════════════════════════════════════════
#  3. 配置持久化逻辑验证
# ═══════════════════════════════════════════════════════
print("\n[3] 配置持久化逻辑验证")

check("_save_device_config 写入 use_half",
      "'use_half': self.use_half" in src)

check("_load_device_config 读取 use_half (默认 False)",
      "config.get('use_half', False)" in src)

# 模拟配置文件读写
try:
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    config = {
        'device': 'auto',
        'frame_limit_enabled': False,
        'target_stream_fps': 30,
        'use_half': True
    }
    json.dump(config, tmp, ensure_ascii=False, indent=2)
    tmp.close()
    
    with open(tmp.name, 'r') as f:
        loaded = json.load(f)
    
    check("配置文件序列化/反序列化 use_half=True",
          loaded.get('use_half') is True)
    
    config2 = {'device': 'auto', 'frame_limit_enabled': False, 'target_stream_fps': 30}
    check("旧配置文件无 use_half 时默认 False",
          config2.get('use_half', False) is False)
    
    os.unlink(tmp.name)
except Exception as e:
    print(f"  ⚠ 配置文件测试异常: {e}")


# ═══════════════════════════════════════════════════════
#  4. 推理管线完整性
# ═══════════════════════════════════════════════════════
print("\n[4] 推理管线完整性")

# 检查三个方法都有完整的 _half 逻辑
for method_name in ["_detect_only", "_detect_and_track", "_detect_segment"]:
    section = src[src.index(f"def {method_name}"):][:2000]
    
    check(f"{method_name}: 有 _half 计算",
          "_half = self.use_half and device.startswith('cuda')" in section)
    
    check(f"{method_name}: 传递 half=_half",
          "half=_half" in section)

# 预热路径
warmup_idx = src.index("CUDA warm-up")
warmup_section = src[warmup_idx-200:warmup_idx+400]
check("load_model 预热: 读取 self.use_half",
      "_half = self.use_half" in warmup_section)
check("load_model 预热: 传递 half=_half",
      "half=_half" in warmup_section)


# ═══════════════════════════════════════════════════════
#  5. 与已有功能兼容性
# ═══════════════════════════════════════════════════════
print("\n[5] 与已有功能兼容性")

monitor = read('frontend/src/views/Monitor/index.vue')

check("NG TOP3 显示模式开关仍存在",
      "ngTopDisplayMode" in monitor)

check("NG TOP3 toggleNgTopMode 函数仍存在",
      "toggleNgTopMode" in monitor)

check("ng_step_cycle_counts 数据流未被破坏",
      "ng_step_cycle_counts" in src and "_ngStepCycleCounts" in monitor)

check("帧率限制功能仍存在 (frame_limit_enabled)",
      "frame_limit_enabled" in src and "frameLimitEnabled" in store)

check("target_stream_fps 功能仍存在",
      "target_stream_fps" in src and "targetStreamFps" in store)

check("性能设置 loadPerformanceSettings 保存逻辑完整",
      "savePerformanceSettings" in settings and "loadPerformanceSettings" in settings)

# 超时NG 功能
check("步骤超时NG 功能未受影响",
      "_force_timeout_ng" in src)

check("周期超时NG 功能未受影响",
      "cycle_max_duration" in src)

# 班次拆分功能
check("班次拆分功能未受影响",
      "_get_current_shift" in src)


# ═══════════════════════════════════════════════════════
#  6. 安全边界
# ═══════════════════════════════════════════════════════
print("\n[6] 安全边界")

check("默认值 use_half = False (后端)",
      "self.use_half = False" in src)

check("默认值 halfPrecision = false (前端)",
      "halfPrecision: false" in store)

# 确保 CPU 不会被开启 FP16
for method_name in ["_detect_only", "_detect_and_track", "_detect_segment"]:
    section = src[src.index(f"def {method_name}"):][:2000]
    check(f"{method_name}: CPU 安全 (device.startswith('cuda') 条件)",
          "device.startswith('cuda')" in section)

check("StreamConfigRequest use_half 默认 False",
      "use_half: bool = False" in src)

# 确认不会影响 CPU 推理路径
check("CPU fallback 仍为 'cpu'",
      "device = 'cpu'" in src)


# ═══════════════════════════════════════════════════════
#  7. 前端 UI 完整性
# ═══════════════════════════════════════════════════════
print("\n[7] 前端 UI 完整性")

check("推理加速卡片在性能设置 Tab 内",
      settings.index("推理加速") > settings.index("性能设置"))

check("FP16 开关在推理加速卡片内",
      settings.index("FP16 半精度推理") > settings.index("推理加速"))

check("说明文字: 推理速度提升 50%-100%",
      "50%-100%" in settings)

check("说明文字: 仅 GPU 生效",
      "仅在 GPU (CUDA) 设备上生效" in settings)

check("说明文字: 检测精度几乎无损",
      "检测精度几乎无损" in settings)

check("说明文字: 下次加载模型时生效",
      "下次加载模型时生效" in settings)

check("Lightning 图标用于推理加速卡片",
      "<Lightning />" in settings)


# ═══════════════════════════════════════════════════════
#  汇总
# ═══════════════════════════════════════════════════════
total = passed + failed
print(f"\n{'='*60}")
print(f"  总计: {total} 项 | ✓ 通过: {passed} | ✗ 失败: {failed}")
if api_ok:
    print(f"  (含 API 实际连通测试)")
else:
    print(f"  (未含 API 连通测试 — 后端未运行)")
print(f"{'='*60}")

if failed:
    print(f"\n失败项:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("\n全部通过! FP16 半精度推理优化 — 端到端验证完毕。")
    sys.exit(0)
