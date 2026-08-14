# Mac 真实摄像头仿真 UAT（v3.51.2 战役归档）

> 2026-08-14 哈金森一拖三"摄像头轮着坏/被占用/画面加载不出但检测在跑"反馈后，
> 用 Mac 内置摄像头对生命周期全路径做的真枪实弹仿真。**顺手抓出并修掉一个主线 bug**：
> `_start_camera_locked` 格式探测 Strategy 3 用"非 Windows"守门，macOS 误入 →
> 好句柄被 release 后用不存在的 CAP_V4L2 重开失败 → 死句柄僵尸态（接口全成功、
> is_running=True、心跳照跳，监控页永远 "No Source"）。修复=守门收紧仅 Linux +
> 重开失败候选兜底 + 终检死句柄显式抛错（v3.51.2, BUG-001）。

## 运行前提

- macOS，系统设置→隐私与安全性→摄像头 给终端宿主授权（未授权时 cv2 打开静默失败）
- 测试后端：`RUNTIME_MODE=test` + tianjun conda env，端口 8001
- `vhelp.py` 复用自 `../virtual_dual_station/`（脚本内已自动加 path）
- S7/S8 需要真实 .pt 模型文件（脚本内路径按需改）

## 剧本 × 断言（共 67 条，2026-08-14 全过）

| 脚本 | 场景 | 断言 |
|---|---|---|
| `vcam_case.py` | S1 正常打开 / S2 外部进程占用 / S3 双通道抢同一物理相机 / S4 运行中调参重开 / S5 快速开停×6 并发竞态 | 12 |
| `vcam_s6.py` | S6 kill -9 强杀重启自动恢复 ×2（第二轮带"重启瞬间打工位数接口"的前端竞态干扰，验证 v3.51 收尾兜底轮） | 7 |
| `vcam_s7_dual_recover.py` | S7 双工位完整恢复：真模型+相机源+检测中 → 强杀 → 源/模型/检测三件套全自动回来、项目绑定不串 | 18 |
| `vcam_s8_no_reset.py` | S8 四连问：双工位绑**不同**模型重启各回各的 / 停止再开始×3 画面必回 / 停止改参数不重置不黑屏 / 邻工位不被殃及 | 22 |

## 踩坑备忘

- Mac 摄像头 AVFoundation **允许多进程共享**，Windows DSHOW 独占——"被占用"在 Mac
  上复现为"都能开"，断言写成"成功共享或明确报占用"二选一。
- `/source/detection/start` 空 body 在停止过源后会报"未加载模型"——这不是 bug：
  真实前端 Monitor 的开始按钮每次都带 `model_path`/`models[]` 重新加载（见
  `startDetectionForChannel`），仿真必须按前端姿势调。
- 快照占位图（"ChN - No Source"）恒 ~10KB 640x480；断言用 >12KB + 解码实际分辨率
  双重判真画面。
- 心跳日志 `FPS=10` 可能是旧值残留（fps_actual 只在成功读帧时刷新），判断读帧
  是否活着要开 `backend.capture` 调试 flag 看采集摘要的窗口 fps。
