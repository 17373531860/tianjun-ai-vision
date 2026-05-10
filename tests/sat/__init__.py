"""SAT (Site Acceptance Testing) — 现场验收测试。

与 tests/e2e_browser 的区别：
- e2e_browser 是开发期的 UI 行为校验（pytest-playwright）。
- sat/ 面向"部署到客户机后"的功能验收，可以打真实后端、真实摄像头、真实模型，
  也支持用 synthetic 路径做无硬件的 API 半自动验收。

约定：
- 所有 SAT 测试默认 SKIP，除非显式设置 RUN_SAT=1。
- 所有用例独立于其他 pytest 套件，不污染 conftest.
"""
