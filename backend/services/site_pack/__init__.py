# ==================== 现场配方包 (.tjvsite) ====================
# 把一台已配好机器的"逻辑配置"（项目/模型/布局/显示/报警/MES 等）打成一个
# 加密文件, 拖到另一台机器的软件上导入。设备绑定 (相机号/COM/IP) 刻意不带,
# 导入后按待办清单在目标机重选。
#
# 模块分工:
#   crypto.py   .tjvsite 二进制格式 + AES-256-GCM 分块加解密 (内置产品密钥 + 可选密码)
#   collect.py  从 DB / DATA_DIR JSON 收集各分域配置 → 内层 zip payload
#   sanitize.py 逐分域剥离机器特定字段 (IP/COM/端口/绝对路径), 并给出导入待办
#   apply.py    校验 + 自动回滚包 + 按稳定身份 (名字/code/key) upsert 回目标机
#
# 不进包的东西 (见 sanitize.py 顶部注释): License / token / 检测历史 / 录像 /
# 相机 index / COM / IP / TRT 引擎 / archive_secret.key。
