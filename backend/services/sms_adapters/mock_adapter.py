"""
Mock 短信适配器 — 试发 / 单测 / 无凭据开发机用, 不发真短信只留痕
"""
from .base import BaseSmsAdapter


class MockSmsAdapter(BaseSmsAdapter):
    provider = "mock"

    # 单测可读取: 最近一次调用入参
    last_call = None

    def send(self, phone_numbers, template_params, config, template_code=None) -> dict:
        MockSmsAdapter.last_call = {
            "phone_numbers": list(phone_numbers or []),
            "template_params": dict(template_params or {}),
            "template_code": self._resolve_template_code(config, template_code),
        }
        print(f"[SMS][mock] 模拟发送 → {phone_numbers}: {template_params}")
        return {"success": True, "error": None,
                "response": {"mock": True, "params": MockSmsAdapter.last_call}}
