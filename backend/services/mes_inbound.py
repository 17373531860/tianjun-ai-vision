"""
外部生产管控系统「入站对接」接收器 (通用可配置)

与 mes_gateway (出站推送) / mes_puller (主动拉取) 互补的第三个方向:
外部系统主动 POST 推一条"开工/任务"过来, 我们接收 → 字段映射 → 校验 → 处理 → 回标准响应。

完全配置驱动, 零客户特异分支 (川南 = 填一份字段映射 + 响应码; 下一家 = 填另一份)。
配置整体存 SystemConfig(key='mes_inbound_config') 的 JSON, 无新表。

处理流程:
  外部 JSON → field_map 反向取值 (复用拉取器同款点路径) → required_fields 校验
  → _apply_task_action (M3 切项目 / M4 建工单 接入点, 当前占位放行)
  → 按 response 配置组装客户要求的业务响应码 (川南 40001-40006)

response 配置可让响应"长成客户要的样子":
{
  "code_field": "code",          # 业务码放哪个键
  "message_field": "message",
  "success_code": 0,             # 成功码
  "success_message": "OK",
  "codes": {                     # 各类失败 → 客户业务码
    "bad_request": 40005, "missing_field": 40004, "duplicate": 40001,
    "unknown_product": 40002, "activate_failed": 40003, "internal_error": 40006,
    "disabled": 40006
  }
}
"""
import json
from typing import Optional

from backend.models.models import SystemConfig
from backend.services.mes_adapters.base import _get_nested, render_template
from backend.core import debug_center


CONFIG_KEY = "mes_inbound_config"

# 默认配置: enabled 默认关 → 接收端点对未配置环境直接回"未启用", 零副作用
DEFAULT_INBOUND_CONFIG = {
    "enabled": False,
    "field_map": {                 # 我们字段 ← 外部字段 (点路径)
        "task_no": "TaskNo",
        "product_code": "ProductCode",
        "step_code": "StepCode",
        "operator": "Operator",
        "begin_time": "BeginTime",
        "is_complete": "IsComplete",   # 完工信号 (配 complete_field 才生效)
        "channel": "Channel",          # 工位号 (配 order_binding=channel 才生效)
    },
    "required_fields": ["task_no", "product_code"],
    "response": {
        "code_field": "code",
        "message_field": "message",
        "success_code": 0,
        "success_message": "OK",
        # 响应体输出格式: json (默认) / xml。xml 时把响应 dict 序列化成 XML 文本。
        "format": "json",
        "xml_root": "response",        # xml 根标签 (SOAP 可填 soap:Envelope 等)
        "xml_declaration": True,       # 是否带 <?xml ...?> 头
        "codes": {
            "bad_request": 40005,
            "missing_field": 40004,
            "duplicate": 40001,
            "unknown_product": 40002,
            "activate_failed": 40003,
            "internal_error": 40006,
            "disabled": 40006,
            "unauthorized": 40007,
        },
    },
    # 入站来源校验 (默认关 = 内网无校验)。开后校验共享密钥头 / IP 白名单。
    "auth": {
        "enabled": False,
        "header_name": "X-API-Key",   # 共享密钥放哪个请求头
        "header_value": "",           # 期望的密钥值 (空 = 不校验头)
        "ip_whitelist": [],           # 允许的来源 IP/CIDR (空 = 不限 IP)
    },
    "max_body_bytes": 1048576,     # 413 上限 (1MB), 0 = 不限
    # 入站报文编码: auto=按 Content-Type 自动识别 / json / form (urlencoded+multipart)
    #            / query (URL 参数) / xml。auto 兜底顺序 json→form→query。
    "input_format": "auto",
    # 是否把 URL query 参数并入报文 (GET 推送 / 混合场景)。body 同名键优先。
    "merge_query_params": True,
    "echo_fields": [],             # 可选: 把哪些已映射字段原样回显进响应 (如 task_no)
    # 开工即按产品码切检测项目。默认关 (与上银包装线 auto_switch_project 默认关对齐):
    # 外部 HTTP 触发的自动切项目会重载模型/打断产线, 属敏感动作, 要的人显式开。
    # 关 = 仅接收登记不切项目。
    "switch_project_on_task": False,
    "product_project_map": {},     # 产品代号 → 检测项目 id (可填对照表)
    # 开工即建/激活工单 (order_no = task_no), 让检测周期绑到该任务、出站报文自带工单号。默认关。
    "create_work_order_on_task": False,
    # 工单绑定方式: project (默认, 绑当前激活项目) / channel (按 channel_field 绑到指定工位)。
    "order_binding": "project",
    # 工位号取哪个映射字段 (order_binding=channel 时用)。
    "channel_field": "channel",
    # 建工单时把所有映射字段 (操作员/开工时间/批次等) 存进工单 extra_data["inbound"] 留痕。默认开。
    "store_mapped_extra": True,
    # 拒绝重复任务: 同 task_no 已存在且在产 (pending/in_progress) 时回 duplicate 码拒收。
    # 默认关 (幂等复用)。与"最新开工为准/顶替"是相反取向, 二选一。
    "reject_duplicate_task": False,
    # 完工信号字段 (已映射字段名, 如 is_complete): 该字段为真时按"完工"收工单, 不再当开工处理。
    # 空 = 不识别完工信号 (只处理开工)。
    "complete_field": "",
    # 完工时用哪个映射字段的值去找工单 (默认 task_no), 配合 complete_match_column。
    "complete_match_field": "task_no",
    # 按工单的哪个列匹配: order_no (默认, 即 task_no=工单号) / product_code (完工报文只带产品码时, 取该产品最新在产单)。
    "complete_match_column": "order_no",
    # 最新开工为准: 收到新开工 → 顶替 (收尾) 当前在产的外部任务工单, 让新任务独占活跃。默认关。
    # (川南: 无配对完工信号, 以最新开工为准, 必须开)
    "supersede_previous_task": False,
    # 顶替范围: project=仅项目绑定的外部在产单(默认) / same_project=仅同项目 / external=所有外部在产单(含工位/集群绑定)。
    "supersede_scope": "project",
    # 顶替时对被顶替工单回传"完工"出站事件 (川南: 发来开工必须反馈完工)。随顶替默认开。
    "report_complete_on_supersede": True,
    # 完工回传的出站事件名 (出站连接 push_events 订阅它即推 task/complete)。
    "complete_event_type": "task_complete",
    # 业务结果 → HTTP 状态码映射。默认空 = 一律 200 (业务码在响应体里)。
    # 有的客户要求失败回非 2xx, 如 {"missing_field": 400, "internal_error": 500}。
    # 键用 code_key: success/bad_request/missing_field/duplicate/unknown_product/
    #               activate_failed/internal_error/disabled。
    "http_status_map": {},
}


def http_status_for(cfg: dict, code_key: str, default: int = 200) -> int:
    """按配置把业务结果 code_key 映射到 HTTP 状态码, 缺省 default。"""
    smap = (cfg or {}).get("http_status_map") or {}
    if code_key in smap:
        try:
            return int(smap[code_key])
        except Exception:
            return default
    return default


def to_xml(data, root_tag: str = "response", declaration: bool = True) -> str:
    """把响应 dict 序列化成 XML 文本 (SOAP/复杂协议响应体用)。

    dict→嵌套标签, list→同名标签重复, None→自闭合, 标量→转义文本。
    标签名取自模板键 (假定合法 XML 名)。
    """
    from xml.sax.saxutils import escape

    def _ser(tag, val):
        if isinstance(val, dict):
            inner = "".join(_ser(k, v) for k, v in val.items())
            return f"<{tag}>{inner}</{tag}>"
        if isinstance(val, list):
            return "".join(_ser(tag, item) for item in val)
        if val is None:
            return f"<{tag}/>"
        return f"<{tag}>{escape(str(val))}</{tag}>"

    payload = data if isinstance(data, dict) else {"value": data}
    body = _ser(root_tag or "response", payload)
    if declaration:
        return '<?xml version="1.0" encoding="UTF-8"?>' + body
    return body


def check_inbound_auth(request, cfg: dict) -> Optional[str]:
    """入站来源校验。返回 None 表示通过, 否则返回失败原因 (字符串)。

    校验共享密钥头 + IP 白名单 (都可选, 默认关全放行)。
    """
    auth = (cfg or {}).get("auth") or {}
    if not auth.get("enabled"):
        return None
    expect = auth.get("header_value")
    if expect:
        hn = auth.get("header_name") or "X-API-Key"
        got = None
        try:
            got = request.headers.get(hn)
        except Exception:
            got = None
        if got != expect:
            return "共享密钥校验失败"
    wl = auth.get("ip_whitelist") or []
    if wl:
        client_ip = None
        try:
            client_ip = request.client.host if request.client else None
        except Exception:
            client_ip = None
        if not _ip_allowed(client_ip, wl):
            return f"来源 IP 不在白名单: {client_ip}"
    return None


def _ip_allowed(ip: Optional[str], whitelist) -> bool:
    if not ip:
        return False
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except Exception:
        # 非标准 IP (如 TestClient 的 'testclient') → 退化成精确字符串匹配
        return ip in [str(e).strip() for e in whitelist]
    for entry in whitelist:
        entry = str(entry).strip()
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except Exception:
            if ip == entry:
                return True
    return False


class MESInbound:
    """配置驱动的入站接收器。无状态, 单例复用。"""

    CONFIG_KEY = CONFIG_KEY

    # ============================================================
    # 配置读写 (存 SystemConfig KV)
    # ============================================================
    def get_config(self, db) -> dict:
        row = db.query(SystemConfig).filter(SystemConfig.key == self.CONFIG_KEY).first()
        cfg = {}
        if row and row.value:
            try:
                cfg = json.loads(row.value)
            except Exception:
                cfg = {}
        return self._with_defaults(cfg if isinstance(cfg, dict) else {})

    def save_config(self, db, cfg: dict) -> dict:
        merged = self._with_defaults(cfg if isinstance(cfg, dict) else {})
        row = db.query(SystemConfig).filter(SystemConfig.key == self.CONFIG_KEY).first()
        value = json.dumps(merged, ensure_ascii=False)
        if row:
            row.value = value
        else:
            db.add(SystemConfig(key=self.CONFIG_KEY, value=value,
                                description="MES 入站对接配置"))
        db.flush()
        return merged

    @staticmethod
    def _with_defaults(cfg: dict) -> dict:
        """浅合并默认值, response/codes/auth 做一层深合并 (用户只填部分也能用)。"""
        out = dict(DEFAULT_INBOUND_CONFIG)
        out.update(cfg or {})
        # response 深合并
        resp = dict(DEFAULT_INBOUND_CONFIG["response"])
        user_resp = (cfg or {}).get("response") or {}
        resp.update({k: v for k, v in user_resp.items() if k != "codes"})
        codes = dict(DEFAULT_INBOUND_CONFIG["response"]["codes"])
        codes.update((user_resp.get("codes") or {}))
        resp["codes"] = codes
        out["response"] = resp
        # auth 深合并
        auth = dict(DEFAULT_INBOUND_CONFIG["auth"])
        auth.update((cfg or {}).get("auth") or {})
        out["auth"] = auth
        return out

    # ============================================================
    # 接收处理 (纯逻辑, 便于单测)
    # ============================================================
    def handle_task_start(self, db, body: dict, cfg: dict) -> dict:
        """处理一条入站开工任务。

        返回 {"response": <按配置组装的客户响应体>, "ok": bool,
              "code_key": str, "mapped": {...}}。
        """
        if not cfg.get("enabled"):
            return self._result(cfg, "disabled", "入站对接未启用")

        mapped = self._map_fields(body, cfg.get("field_map") or {})

        missing = [f for f in (cfg.get("required_fields") or []) if not mapped.get(f)]
        if missing:
            return self._result(cfg, "missing_field",
                                f"缺少必填字段: {', '.join(missing)}", mapped=mapped)

        try:
            ok, err_key, msg = self._apply_task_action(db, mapped, cfg)
        except Exception as e:
            debug_center.dbg("backend.mes", "入站任务处理异常", f"mapped={mapped} err={e}")
            return self._result(cfg, "internal_error", f"内部处理异常: {e}", mapped=mapped)

        if not ok:
            return self._result(cfg, err_key or "internal_error",
                                msg or "处理失败", mapped=mapped)

        success_msg = (cfg.get("response") or {}).get("success_message") or "OK"
        return self._result(cfg, "success", success_msg, mapped=mapped, success=True)

    def _apply_task_action(self, db, mapped: dict, cfg: dict):
        """开工任务的实际处理 (编排)。返回 (ok, error_key, message)。

        三块均按配置开关 opt-in, 默认全关 (开箱即用只接收登记不动运行态):
          - 完工信号优先: complete_field 命中真值 → 收工单 (不再当开工处理)
          - switch_project_on_task: 按产品代号切检测项目 (复用 activate_project_core)
          - create_work_order_on_task: 建/激活工单 (order_no=task_no), 让周期绑该任务
        """
        complete_field = cfg.get("complete_field")
        if complete_field and self._truthy(mapped.get(complete_field)):
            return self._complete_work_order(db, mapped, cfg)

        # 拒绝重复任务 (可选): 同 task_no 已在产则回 duplicate
        if cfg.get("reject_duplicate_task", False):
            task_no = str(mapped.get("task_no") or "").strip()
            if task_no:
                from backend.services.work_order import WorkOrderService
                existing = WorkOrderService().get_order_by_no(db, task_no)
                if existing and existing.status in ("pending", "in_progress"):
                    return (False, "duplicate",
                            f"任务 {task_no} 已存在且在产, 拒绝重复开工")

        msgs = []
        if cfg.get("switch_project_on_task", False):
            ok, key, msg = self._switch_project(db, mapped, cfg)
            if not ok:
                return (False, key, msg)
            msgs.append(msg)

        if cfg.get("create_work_order_on_task", False):
            ok, key, msg = self._ensure_work_order(db, mapped, cfg)
            if not ok:
                return (False, key, msg)
            msgs.append(msg)

        return (True, None, "; ".join(msgs) if msgs else "ok")

    def _switch_project(self, db, mapped: dict, cfg: dict):
        """按产品代号切检测项目, 复用 projects.activate_project_core (与手动激活一致)。"""
        product_code = str(mapped.get("product_code") or "").strip()
        if not product_code:
            return (False, "unknown_product", "缺少产品代号, 无法匹配检测项目")

        pmap = cfg.get("product_project_map") or {}
        project_id = pmap.get(product_code)
        if project_id is None:
            return (False, "unknown_product",
                    f"产品代号 {product_code} 未配置对应检测项目")
        try:
            project_id = int(project_id)
        except Exception:
            return (False, "unknown_product",
                    f"产品代号 {product_code} 映射的项目 id 非法: {project_id}")

        # 已经是激活项目 → 跳过重载, 避免无谓切模型打断正在跑的产线
        from backend.models.models import Project
        active = db.query(Project).filter(Project.is_active == True).first()
        if active and active.id == project_id:
            return (True, None, f"产品 {product_code} 对应项目#{project_id} 已激活")

        try:
            from backend.api.projects import activate_project_core
            activate_project_core(db, project_id)
        except Exception as e:
            # activate_project_core 项目不存在抛 HTTPException(404)
            if getattr(e, "status_code", None) == 404:
                return (False, "unknown_product",
                        f"产品 {product_code} 映射的检测项目#{project_id} 不存在")
            return (False, "activate_failed", f"切换检测项目失败: {e}")

        return (True, None, f"已切换到产品 {product_code} 的检测项目#{project_id}")

    def _ensure_work_order(self, db, mapped: dict, cfg: dict):
        """建/激活工单 (order_no = task_no), 绑到当前激活项目, 置 in_progress 供周期绑定。"""
        task_no = str(mapped.get("task_no") or "").strip()
        if not task_no:
            return (True, None, "无 task_no, 跳过工单")

        from backend.services.work_order import WorkOrderService
        from backend.models.models import Project
        svc = WorkOrderService()
        order = svc.get_order_by_no(db, task_no)
        if order is None:
            extra = {"inbound": dict(mapped)} if cfg.get("store_mapped_extra", True) else None
            data = {
                "order_no": task_no,
                "product_name": str(mapped.get("product_code") or task_no),
                "product_code": mapped.get("product_code"),
                "product_spec": mapped.get("product_spec"),
                "source": "external",
                "status": "pending",
                "created_by": mapped.get("operator"),
                "extra_data": extra,
            }
            # 绑定方式: 工位 (按 channel_field) 或 项目 (当前激活项目)
            if (cfg.get("order_binding") or "project").lower() == "channel":
                ch = self._parse_channel(mapped.get(cfg.get("channel_field") or "channel"))
                if ch is not None:
                    data["binding_scope"] = "channels"
                    data["target_channels"] = [ch]
                else:
                    return (False, "bad_request", "工位路由模式但报文缺/非法工位号")
            else:
                active = db.query(Project).filter(Project.is_active == True).first()
                data["binding_scope"] = "project"
                data["project_id"] = active.id if active else None
            try:
                order = svc.create_order(db, data)
            except Exception as e:
                return (False, "internal_error", f"建工单失败: {e}")

        self._ensure_in_progress(svc, db, order)

        # 最新开工为准: 顶替当前在产的其它外部任务 (并对其回传完工)
        superseded = 0
        if cfg.get("supersede_previous_task", False):
            superseded = self._supersede_previous_tasks(db, order, cfg)

        suffix = f", 顶替旧任务 {superseded} 个" if superseded else ""
        return (True, None, f"工单 {task_no} 已就绪{suffix}")

    def _supersede_previous_tasks(self, db, new_order, cfg) -> int:
        """收到新开工 → 把当前在产的其它外部任务工单收尾, 并对每个回传完工出站。

        范围按 supersede_scope 配置:
          - project (默认): binding_scope=project 的外部在产单 (跨产品换型旧单也在另一项目, 全收)
          - same_project: 仅与新单同 project_id
          - external: 所有 source=external 在产单 (含工位/集群绑定)
        返回被顶替的数量。
        """
        from backend.models.mes_models import WorkOrder
        from backend.services.work_order import WorkOrderService
        svc = WorkOrderService()
        scope = (cfg.get("supersede_scope") or "project").lower()
        q = db.query(WorkOrder).filter(
            WorkOrder.status == "in_progress",
            WorkOrder.source == "external",
            WorkOrder.id != new_order.id)
        if scope == "project":
            q = q.filter(WorkOrder.binding_scope == "project")
        elif scope == "same_project":
            q = q.filter(WorkOrder.project_id == new_order.project_id)
        elif scope == "same_channel":
            # 仅顶替与新单工位有交集的工位绑定单 (多工位各自独立"最新开工为准")
            new_ch = set(new_order.target_channels or [])
            q = q.filter(WorkOrder.binding_scope == "channels")
            prev = [o for o in q.all() if set(o.target_channels or []) & new_ch]
            if not prev:
                return 0
            return self._finish_superseded(db, svc, prev, cfg)
        # external/all: 不加额外过滤
        prev = q.all()
        if not prev:
            return 0
        return self._finish_superseded(db, svc, prev, cfg)

    def _finish_superseded(self, db, svc, prev, cfg) -> int:

        done = []
        for o in prev:
            try:
                svc.change_status(db, o.id, "completed")
                done.append(o.id)
            except Exception as e:
                debug_center.dbg("backend.mes", "顶替收尾失败",
                                 f"order={o.order_no} status={o.status} err={e}")
        # 先提交释放 SQLite 写锁, 再做网络回传 (与 mes_hooks cycle_end 同模式, 避免锁等待)
        try:
            db.commit()
        except Exception:
            db.rollback()

        if cfg.get("report_complete_on_supersede", True):
            for oid in done:
                self._report_order_complete(db, oid, cfg)
        return len(done)

    @staticmethod
    def _report_order_complete(db, order_id: int, cfg: dict):
        """对一张工单回传"完工"出站事件 (订阅 complete_event_type 的连接会推 task/complete)。"""
        try:
            from backend.services.mes_gateway import get_mes_gateway
            from backend.services.work_order import WorkOrderService
            summary = WorkOrderService().get_order_summary(db, order_id)
            if not summary:
                return
            event = cfg.get("complete_event_type") or "task_complete"
            ctx = {"order": summary, "event": event}
            get_mes_gateway().dispatch(event, ctx, None)
        except Exception as e:
            debug_center.dbg("backend.mes", "完工回传失败",
                             f"order_id={order_id} err={e}")

    def _complete_work_order(self, db, mapped: dict, cfg: dict):
        """完工信号: 按配置的匹配键找工单并收为 completed。找不到/已终态则静默放行。

        匹配键可配: complete_match_field(取哪个映射字段的值) + complete_match_column
        (order_no=工单号 / product_code=该产品最新在产单)。
        """
        match_field = cfg.get("complete_match_field") or "task_no"
        match_col = (cfg.get("complete_match_column") or "order_no").lower()
        value = str(mapped.get(match_field) or "").strip()
        if not value:
            return (True, None, f"完工信号但无 {match_field}, 跳过")

        from backend.services.work_order import WorkOrderService
        from backend.models.mes_models import WorkOrder
        svc = WorkOrderService()
        if match_col == "product_code":
            order = (db.query(WorkOrder)
                     .filter(WorkOrder.product_code == value,
                             WorkOrder.status == "in_progress",
                             WorkOrder.source == "external")
                     .order_by(WorkOrder.id.desc()).first())
        else:
            order = svc.get_order_by_no(db, value)

        if order is None:
            return (True, None, f"完工: 未找到匹配工单 ({match_col}={value}), 跳过")
        if order.status == "in_progress":
            try:
                svc.change_status(db, order.id, "completed")
            except Exception as e:
                return (False, "internal_error", f"工单收尾失败: {e}")
        return (True, None, f"工单 {order.order_no} 已完工")

    @staticmethod
    def _ensure_in_progress(svc, db, order):
        """把工单安全迁到 in_progress (状态机: draft→pending→in_progress; paused→in_progress)。

        completed/cancelled 终态不强行复活; 任何迁移失败仅打日志, 不阻断接收。
        """
        try:
            if order.status == "draft":
                svc.change_status(db, order.id, "pending")
            if order.status in ("pending", "paused"):
                svc.change_status(db, order.id, "in_progress")
        except Exception as e:
            debug_center.dbg("backend.mes", "工单激活状态迁移失败",
                             f"order={getattr(order, 'order_no', '?')} "
                             f"status={getattr(order, 'status', '?')} err={e}")

    @staticmethod
    def _parse_channel(v):
        """把工位号解析成 int, 失败返回 None。"""
        if v is None:
            return None
        try:
            return int(str(v).strip())
        except Exception:
            return None

    @staticmethod
    def _truthy(v) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        return str(v).strip().lower() in (
            "1", "true", "yes", "y", "t", "是", "完工", "complete", "completed")

    # ============================================================
    # 子步骤
    # ============================================================
    @staticmethod
    def _map_fields(body: dict, field_map: dict) -> dict:
        """外部报文 → 我们的字段。field_map = {我们字段: 外部字段点路径}。"""
        out = {}
        if not isinstance(body, dict):
            return out
        for our_field, ext_path in (field_map or {}).items():
            if not ext_path:
                continue
            val = _get_nested(body, ext_path)
            if val is not None:
                out[our_field] = val
        return out

    def _result(self, cfg: dict, code_key: str, message: str,
                mapped: Optional[dict] = None, success: bool = False) -> dict:
        return {
            "response": self.build_response(cfg, code_key, message, mapped),
            "ok": success,
            "code_key": code_key,
            "mapped": mapped or {},
        }

    def error_response(self, cfg: dict, code_key: str, message: str) -> dict:
        """给 API 层在解析失败等场景直接拿响应体用。"""
        return self.build_response(cfg, code_key, message)

    @staticmethod
    def _set_nested(d: dict, dotted: str, value):
        """按点路径写入嵌套 dict: 'head.code' → d['head']['code']=value。"""
        keys = (dotted or "").split(".")
        cur = d
        for k in keys[:-1]:
            nxt = cur.get(k)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[k] = nxt
            cur = nxt
        cur[keys[-1]] = value

    @staticmethod
    def build_response(cfg: dict, code_key: str, message: str,
                       mapped: Optional[dict] = None) -> dict:
        """组装回给外部系统的响应体。

        两种模式:
          - response.template (自研 {key.path} 模板, 复用出站引擎): 全自定义信封,
            支持嵌套/数组, 适配 SOAP/复杂协议。占位符: {code} {message} {code_key}
            {success} 及映射字段 {task_no} / {mapped.task_no}。
          - 无 template: code_field/message_field 支持点路径 (如 head.code) 嵌套写入。
        """
        rc = (cfg or {}).get("response") or {}
        codes = rc.get("codes") or {}
        success = (code_key == "success")
        if success:
            code = rc.get("success_code", 0)
        else:
            code = codes.get(code_key, codes.get("internal_error", 40006))

        template = rc.get("template")
        if template:
            ctx = {**(mapped or {}), "mapped": mapped or {},
                   "code": code, "message": message,
                   "code_key": code_key, "success": success}
            rendered = render_template(template, ctx)
            if isinstance(rendered, dict):
                for f in (cfg.get("echo_fields") or []):
                    if mapped and f in mapped and f not in rendered:
                        rendered[f] = mapped[f]
            return rendered

        code_field = rc.get("code_field") or "code"
        msg_field = rc.get("message_field") or "message"
        resp: dict = {}
        MESInbound._set_nested(resp, code_field, code)
        MESInbound._set_nested(resp, msg_field, message)
        for f in (cfg.get("echo_fields") or []):
            if mapped and f in mapped:
                resp[f] = mapped[f]
        return resp


_inbound_instance: Optional[MESInbound] = None


def get_mes_inbound() -> MESInbound:
    global _inbound_instance
    if _inbound_instance is None:
        _inbound_instance = MESInbound()
    return _inbound_instance
