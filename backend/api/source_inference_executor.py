"""InferenceExecutor 组件 (v2.7.16 P7 第五刀, 组合优于继承)。

把"持久推理线程池"子领域从 VSM 抽出. 此前是散在 VSM 上的 1 字段 + 2 方法,
被 detect_runners / lifecycle 等多处 mixin 重复调用, 抽出来语义更清晰.

数据所有权:
  - 旧: VSM._inference_executor (字段) + 2 方法散落
  - 新: InferenceExecutor 自持 _executor (ThreadPoolExecutor 单例)

公共 API:
  get()       : 取持久线程池, 懒初始化 (max_workers=1)
  shutdown()  : 关闭线程池, 重置为 None

历史方法名兼容 (通过 VSM.__getattr__ 转发):
  _get_inference_executor      → get
  _shutdown_inference_executor → shutdown
历史字段兼容 (通过 VSM.__getattr__/__setattr__ 转发):
  _inference_executor          → _executor (内部)
"""
from concurrent.futures import ThreadPoolExecutor


class InferenceExecutor:
    def __init__(self):
        self._executor = None  # 内部持久线程池

    @property
    def _inference_executor(self):
        """兼容 vm._inference_executor 历史字段名 (通过 VSM 兼容层透传到本属性)"""
        return self._executor

    @_inference_executor.setter
    def _inference_executor(self, value):
        self._executor = value

    def get(self):
        """获取持久推理线程池 (懒初始化, 避免每帧创建新线程池)"""
        if self._executor is None or self._executor._shutdown:
            self._executor = ThreadPoolExecutor(max_workers=1)
        return self._executor

    def shutdown(self):
        """关闭推理线程池"""
        if self._executor is not None:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass
            self._executor = None
