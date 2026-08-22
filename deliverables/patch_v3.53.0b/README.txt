天军 AI 视觉 v3.53.0b 捷昌累积现场热补丁
============================================

适用范围
--------
仅适用于 ProductVersion 精确为 3.53.0/3.53.0.0，且当前前端为实际
v3.53.0a 全量 dist 的安装目录。它不是 v3.54，不修改版本号、数据库、项目配置、
现场参数或 CORE .pyd，也不会修复既有 orphan cycle。

累积内容
--------
1. 保留 v3.53.0a 人工合格放行；整组范围使用等灯扫码枪广播检测面与
   synchronized_all_ok ChannelGroup 成员并集，跳过已结算工位，并在结算锁内二次 gate。
2. 扫码 Task A：物理去重、解析失败、external_only 三条 LOFF 早退路径自动复灯。
3. 保留 PATCH_JC353_SCANNER_E_R1：E 模式 generation + 2 秒快速 OK stale relock guard。
4. 工单 Task D：按项目筛选时保留 channels/cluster；多工位默认关闭“仅当前项目”，
   单工位保持开启；priority 默认 3。
5. PostgreSQL cycle_end：只在 dialect=postgresql 时让 lifecycle 模块 now/fromtimestamp
   返回本地 aware datetime；SQLite 不注入。

安装
----
1. 完全解压到一个新的空目录，不要与旧补丁目录混放。
2. 双击 install_v3.53.0b.bat。需要时以管理员身份运行。
3. 脚本在停止进程前完成：补丁 payload hash、ProductVersion、旧 a backend hash、
   旧 a 33 文件 dist manifest 的全部校验。任何一项不符都会停止且不改文件。
4. 校验通过后，脚本只停止可确认位于安装目录内的 TianJun/Electron/捆绑 Python，
   不会无差别 taskkill 系统的全部 python.exe。
5. 脚本把立即安装前的 hotfix.py、manual_pass_v3530a.py、完整 dist 备份到：
   resources\hotfix_backups\v3530b
   不覆盖旧 .bak_v3530。安装失败会自动恢复此备份。
6. 正常启动天军，再双击 check_patch.bat；保留显示 exact/OK 的截图。

一键回滚
--------
双击 rollback_v3.53.0b.bat。脚本先校验备份和当前 b payload，再恢复这次安装前的
精确 backend/dist 并逐文件复核。备份目录会保留，不修改 DB/config。

隔离沙箱
--------
测试时可设置环境变量 TIANJUN_PATCH_INSTALL_DIR 指向隔离安装副本。该变量只改变
目标目录，不绕过 ProductVersion、旧 a hash 或 manifest 校验。

已知输入证据
------------
实际旧 a ZIP SHA256:
D65250BAAE168DC3BFAAD6A62C45F50F14997BF0AEA72AE7B76D85A2C003A662
实际旧 a hotfix.py SHA256:
2DCCA7B05887FD2B06846C649789691C26C6D89C31C6DEB12E221DA5283F3213
实际旧 a manual_pass_v3530a.py SHA256:
F9D3B5BA6DFD982ED8322017934AD1CBE1809DD70CED114F9285828BF61C4EC5
实际旧 a dist: 33 文件 / 4615393 bytes；index SHA256:
2032B42D6B0099E2530099C798AA34AA398C58276CF14F94522CCAD8DE7D044F

边界说明
--------
本包前端以 513856b 基线手工移植旧 a + B + D 构建；由于旧 a 源仅存在于带后续
任务 C 的 recovery 未提交工作区，没有得到 old-a-only 的可重放源码树，因此没有
伪报“从 513856b 重建出旧 a 相同 asset hash”。实际旧 a 成品 manifest 被原样固化，
安装前会严格核对。本包未引入任务 C 新增的 regionEventRuleSteps 或自定义
data-layout 差异；513856b 基线既有的 stepInflightDurations 计时逻辑保持不变。

验证边界
--------
本地单元/运行时 monkey patch 回归与 Vite build 不等于真实现场验收。
Sol 已独立用 Python 3.10 + Nuitka 2.7.16 最小 .pyd 验证 module.datetime 全局重绑
对编译函数立即生效；并已在 ephemeral PostgreSQL 隔离容器（非现场库）用实际 hotfix、
lifecycle 与 TIMESTAMPTZ 完成 start/end aware 和 duration 正值验证。以上均非现场验收，
本补丁也不会修复既有 orphan cycle。
