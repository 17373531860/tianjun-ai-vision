# ==================== backend.api 包 ====================
# 主程序路由的唯一挂载登记处是 backend/api/router_manifest.py（OVERLAP-3 治理, 2026-07）。
# 本包 __init__ 刻意保持空:
#   - 不在这里聚合 router —— 否则 `import backend.api.任意子模块` 都会连带导入全部路由模块
#     （source.py 等一导入就拉起 cv2/PIL/numpy, 拖慢测试且可能踩 cv2 导入顺序红线）
#   - 需要单个路由做轻量测试时, 直接 `from backend.api import <模块>` 取 <模块>.router
