"""sensor-clean 全栈插件入口（传感器清洁工序定制）。

业务背景：
- 视角1 检测「查看产品有无脏污 / 擦拭产品」两个动作，按动作生命周期计一件产品。
- 视角2 检测「更换棉签」动作。
- 一根棉签擦满 K 个产品后必须更换，否则锁定计数并提示；视角2 检出换棉签后解锁。

架构（帧级复刻式）：
- 检测推理复用主程序引擎（多通道 + 模型加载），但产品计数由插件按 demo 的
  ProductCounter 算法逐帧自计算，挂在主程序 detection_frame 帧级钩子上。
- 这样能逐件复刻 demo 计数精度（demo 计数依赖逐帧锚动作生命周期, 主程序周期结算
  机制不同, 无法逐件对齐, 故改为插件接管计数）。
- 配套一键应用「对齐 demo」的项目配置模板（见 routes / preset）。
"""
from .hooks import on_detection_frame, set_host
from .routes import router


def register_plugin(app, registry, license_payload, host):
    """主程序 PluginManager 调用的注册入口（4 参契约）。"""
    set_host(host)
    # 查询棉签状态 + 一键应用项目配置模板的接口
    registry.routes.include_router(router, subpath="swab")
    # 监听帧级检测：视角1 逐帧产品计数累加 / 达 K 锁定 / 视角2 换棉签解锁
    registry.hooks.register(
        hook_type="detection_frame",
        phase="post_inference",
        when="post",
        priority=100,
        handler=on_detection_frame,
    )
    return {
        "name": "sensor-clean",
        "customer_code": license_payload.get("customerName"),
    }
