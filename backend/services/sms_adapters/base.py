"""
短信适配器基类 (每日短信日报, v3.46+)

参考 mes_adapters 的注册表模式:
  - 子类实现 send(), 返回统一结果 dict
  - 凭据/签名/模板等放 config dict (来源 SystemConfig sms.config)

国内云短信共同现实约束:
  - 必须用平台预先审核的"签名 + 模板", 内容是模板变量而非自由文本
  - 变量长度有限制 (阿里云默认 1~20 字符/变量)
"""
from typing import Dict, List, Any


class BaseSmsAdapter:
    """短信适配器基类, 子类实现 send()"""

    # 子类覆盖: 服务商标识
    provider = "base"

    def send(self, phone_numbers: List[str], template_params: Dict[str, Any],
             config: dict, template_code: str = None) -> dict:
        """发送模板短信。

        Args:
            phone_numbers: 手机号列表 (国内 11 位, 不带 +86; 适配器自行按平台要求加前缀)
            template_params: 模板变量 dict (有序; 腾讯云按插入顺序转位置参数)
            config: 服务商配置 (access_key_id / access_key_secret / sign_name /
                    template_code / region 等, 各适配器文档字段)
            template_code: 覆盖 config 里默认模板 code (规则级覆盖)

        Returns:
            {"success": bool, "error": str|None, "response": dict|None}
            response 是服务商原始响应摘要, 存 sms_send_logs 排查用。
            实现必须自行捕获网络/签名异常并转成 success=False, 不向上抛。
        """
        raise NotImplementedError

    def _resolve_template_code(self, config: dict, template_code: str = None) -> str:
        return (template_code or config.get("template_code") or "").strip()

    @staticmethod
    def _stringify_params(template_params: Dict[str, Any]) -> Dict[str, str]:
        """云平台模板变量一律要求字符串"""
        return {k: str(v) for k, v in (template_params or {}).items()}
