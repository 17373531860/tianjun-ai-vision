"""
外部 MES 适配器基类 + 模板渲染引擎

模板语法:
  "{workpiece.serial_no}"  → 从上下文取值
  "{extra.weight}"         → 从额外字段取值
  数组: {"_array_source": "steps", "_item_template": {...}}
"""
import re
from typing import Any


def _get_nested(data: dict, dotted_key: str, default=None):
    """从嵌套字典取值: 'order.order_no' → data['order']['order_no']"""
    keys = dotted_key.split(".")
    cur = data
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k, default)
        else:
            return default
    return cur


def render_template(template: Any, context: dict) -> Any:
    """递归渲染模板: 替换 {x.y} 占位符, 展开数组模板"""
    if isinstance(template, str):
        pattern = re.compile(r"\{([^}]+)\}")
        full_match = pattern.fullmatch(template)
        if full_match:
            return _get_nested(context, full_match.group(1))
        def replacer(m):
            val = _get_nested(context, m.group(1), "")
            return str(val) if val is not None else ""
        return pattern.sub(replacer, template)

    if isinstance(template, dict):
        if "_array_source" in template:
            source_key = template["_array_source"]
            item_tpl = template.get("_item_template", {})
            items = _get_nested(context, source_key) or []
            if not isinstance(items, list):
                items = []
            result = []
            for item in items:
                item_ctx = {**context, "item": item}
                result.append(render_template(item_tpl, item_ctx))
            return result
        return {k: render_template(v, context) for k, v in template.items()}

    if isinstance(template, list):
        return [render_template(item, context) for item in template]

    return template


class BaseAdapter:
    """适配器基类, 子类实现 send() 方法"""

    def build_payload(self, context: dict, config: dict) -> Any:
        """根据模板配置 + 上下文数据构建请求体"""
        template = config.get("template", {})
        return render_template(template, context)

    def send(self, payload: Any, config: dict) -> dict:
        """发送数据到外部 MES, 返回 {status_code, body, success, error}"""
        raise NotImplementedError

    def check_response(self, response: dict, config: dict) -> bool:
        """检查响应是否表示成功"""
        check = config.get("success_check")
        if not check:
            return response.get("status_code", 0) in (200, 201)
        body = response.get("body")
        if not isinstance(body, dict):
            return False
        field = check.get("field", "success")
        expect = check.get("expect", True)
        return _get_nested(body, field) == expect
