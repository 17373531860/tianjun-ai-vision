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

# 完工信号默认"真值"词表 (complete_true_words 留空时使用)
DEFAULT_TRUE_WORDS = frozenset((
    "1", "true", "yes", "y", "t", "是", "完工", "complete", "completed"))

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
            # 报警消除: 未找到对应在途报警 (川南 v4 约定 40007)
            "alarm_not_found": 40007,
        },
        # 各 code_key → 固定响应文案覆盖。空 = 用代码内置默认文案 (含动态内容如缺失字段名)。
        # 键同 codes (success/bad_request/missing_field/duplicate/unknown_product/
        #            activate_failed/internal_error/disabled/unauthorized/alarm_not_found)。
        "messages": {},
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
    # 对照表无匹配时, 兜底按"检测项目名 == 产品代号"自动匹配 (川南 v4: 命名完全一致)。
    # 默认关 (新功能不影响存量客户, 仅认 product_project_map); 开 = 项目直接以产品代号命名即可零配置切换。
    "match_project_by_name": False,
    # 自动同名匹配的"严格边界"档: 开 = 项目名命中处须贴串首/尾或分隔符 (防 HG 误吞 HGH20);
    # 关(默认) = 仅靠"取最长命中"压歧义, 但能覆盖无分隔符场景(HGH20001 命中 HGH20)。仅 match_project_by_name 开时生效。
    "name_match_strict_boundary": False,
    # 开工后自动开始检测 (默认关): 任务处理成功后, 对"视频源在跑 + 模型就绪 + 未在检测"
    # 的工位自动拉起检测 (门槛与开机自动恢复一致)。启动失败不影响开工响应, 只记调试日志。
    # 关 = 检测启动权留在现场 (推荐产线检测常开, 开工只热切项目)。
    "start_detection_on_task": False,
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
    # 完工信号"真值"词表 (命中其一即视为完工)。空 = 用内置默认词表。
    # 大小写不敏感; 布尔 true / 非零数字恒为真。
    "complete_true_words": [],
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
    # 额外入站接收路径别名 (除内置 /api/v1/mes/inbound/* 外, 客户可在根路径自定义接收 URL)。
    # 每项: {"path": "/warning/clear", "action": "alarm_clear", "methods": ["POST","GET"]}
    # action ∈ task_start / alarm_clear / health。启动时动态注册; 改后即时生效 (增删改全支持)。
    # path 必须以 / 开头, 不允许 /api/ 前缀 (防劫持主程序 API)。
    "receive_paths": [],
    # 业务结果 → HTTP 状态码映射。默认空 = 一律 200 (业务码在响应体里)。
    # 有的客户要求失败回非 2xx, 如 {"missing_field": 400, "internal_error": 500}。
    # 键用 code_key: success/bad_request/missing_field/duplicate/unknown_product/
    #               activate_failed/internal_error/disabled/alarm_not_found。
    "http_status_map": {},
    # 产品代号未匹配到检测项目时的返回文案 (川南 v4 第 1 条要求固定话术)。
    "unknown_product_message": "未查询到当前产品代号检测模型",
    # ── 报警消除 / 在途报警台账 (默认惰性: alarm_event_name 为空时网关不登记任何台账) ──
    # 出站推送时, 哪个事件名视为"报警事件" → 成功推出后登记一条在途报警 (空 = 不登记)。
    # 支持单个 (如 "cycle_end") 或多个 (["cycle_end","box_timeout"] / "cycle_end,box_timeout")。
    # 任何经网关分发的事件源都能当报警源: cycle_end / session_end / box_complete /
    # box_timeout / weight_no_barcode / 插件主动触发 / 包装结算 …… 全配置驱动, 不改代码。
    "alarm_event_name": "",
    # 登记台账的结果过滤: 仅当事件结果在此列表内才登记 (默认只登 NG, 避免 OK 周期误当报警)。
    # 空列表 = 不过滤 (任何结果都登记)。结果取自 context: cycle.result / overall_result / result。
    "alarm_record_on_results": ["NG"],
    # 出站报警事件的 context → 台账字段映射 (我们字段 ← context 点路径)。默认对齐 cycle_end 上下文:
    # 任务号/产品号取自激活工单 (开工时入站建的), 工序工步/操作员取自工单留痕, 报警原因取 NG 原因。
    "alarm_ledger_field_map": {
        "task_no": "order.order_no",
        "product_code": "order.product_code",
        "step_code": "order.extra_data.inbound.step_code",
        "operator": "order.extra_data.inbound.operator",
        "warning_text": "cycle.ng_reason",
    },
    # 报警去重窗口 (秒): 同唯一键报警在该窗口内只登记一次。客户可配 (川南 v4 第 4 条)。0 = 不去重。
    "alarm_dedup_sec": 5,
    # 报警消除按哪几个字段匹配在途报警 (川南 v4: 任务号_产品号_工序工步_操作员)。
    "alarm_clear_match_fields": ["task_no", "product_code", "step_code", "operator"],
    # 监控页"在途报警持续横幅"的外观/行为配置 (前端 ExternalAlarmBanner 读取, 全可配)。
    "alarm_banner": {
        "enabled": True,            # 总开关: 关 → 监控页不显示横幅 (即使有在途报警)
        "position": "top",          # top / bottom: 横幅停靠位置
        "color": "#dc2626",         # 主色 (背景渐变基色), 客户可换品牌色
        "poll_interval_sec": 3,     # 轮询在途报警间隔 (秒)
        "show_task_no": True,       # 横幅每条是否展示 任务号
        "show_product_code": True,  # ... 产品号
        "show_step_code": True,     # ... 工序工步
        "show_operator": True,      # ... 操作员
        "show_time": True,          # ... 报警时间
        # v3.39 川南反馈: 上游没回推消除命令时报警会一直挂着, 软件内没有任何出口。
        # 两个出口都默认关 (保持"只能外部消除"的对接契约), 客户按需打开:
        "allow_manual_clear": False,      # 横幅上显示"手动消除"按钮 (调试/联调用)
        "clear_on_counter_reset": False,  # 监控页"清零"时顺带消除全部在途报警
    },
    # 监控页"开工后主界面任务信息条"的逐要素显示开关 (前端 Monitor 读取)。
    # 默认全关 = 维持原界面, 信息条不追加任何标签; 客户按需逐项打开 (川南 v1 第 1 条:
    # 开工后界面持续显示 任务号/产品代号/工序工步/操作员)。数据取活跃工单 extra_data.inbound。
    "task_info_display": {
        "show_task_no": False,       # 信息条追加"任务号"标签
        "show_product_code": False,  # ... 产品代号
        "show_step_code": False,     # ... 工序工步
        "show_operator": False,      # ... 操作员
        # v3.39 川南反馈: 信息条一行挤不下被截断。两个显示层开关, 默认维持原样:
        "show_order_chip": True,     # 工单徽标 (单号+进度+良率); 关 = 信息条不显示工单块
        "two_line_layout": False,    # 开 = 任务要素改为"表头一行+信息一行"的表格式布局
    },
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
        # alarm_banner 深合并 (客户只改部分外观项也能保留其余默认)
        banner = dict(DEFAULT_INBOUND_CONFIG["alarm_banner"])
        banner.update((cfg or {}).get("alarm_banner") or {})
        out["alarm_banner"] = banner
        # task_info_display 深合并 (客户只开部分要素也能保留其余默认)
        tinfo = dict(DEFAULT_INBOUND_CONFIG["task_info_display"])
        tinfo.update((cfg or {}).get("task_info_display") or {})
        out["task_info_display"] = tinfo
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
            debug_center.dbg("backend.mes", "入站开工被拒", "入站对接未启用 (enabled=false)")
            return self._result(cfg, "disabled", "入站对接未启用")

        mapped = self._map_fields(body, cfg.get("field_map") or {})
        debug_center.dbg("backend.mes", "收到入站开工",
                         f"task_no={mapped.get('task_no')} product={mapped.get('product_code')} "
                         f"step={mapped.get('step_code')} operator={mapped.get('operator')}")

        missing = [f for f in (cfg.get("required_fields") or []) if not mapped.get(f)]
        if missing:
            debug_center.dbg("backend.mes", "入站开工缺必填字段", f"缺: {', '.join(missing)}")
            return self._result(cfg, "missing_field",
                                f"缺少必填字段: {', '.join(missing)}", mapped=mapped)

        try:
            ok, err_key, msg = self._apply_task_action(db, mapped, cfg)
        except Exception as e:
            debug_center.dbg("backend.mes", "入站任务处理异常", f"mapped={mapped} err={e}")
            return self._result(cfg, "internal_error", f"内部处理异常: {e}", mapped=mapped)

        if not ok:
            debug_center.dbg("backend.mes", "入站开工处理失败",
                             f"code={err_key} msg={msg} task_no={mapped.get('task_no')}")
            return self._result(cfg, err_key or "internal_error",
                                msg or "处理失败", mapped=mapped)

        debug_center.dbg("backend.mes", "入站开工完成", f"{msg} (task_no={mapped.get('task_no')})")
        success_msg = (cfg.get("response") or {}).get("success_message") or "OK"
        # v3.39: 处理明细附在成功文案后 (业务码不变, 上游按 code 判成败不受影响)。
        # 川南反馈逼出的可观测性: "开工自动开始检测"没拉起时, 原因直接可见,
        # 不用开调试日志。与 handle_alarm_clear 的"OK (已消除 N 条)"同款先例。
        if msg and msg != "ok":
            success_msg = f"{success_msg} ({msg})"
        return self._result(cfg, "success", success_msg, mapped=mapped, success=True)

    def handle_alarm_clear(self, db, body: dict, cfg: dict) -> dict:
        """处理一条入站"报警消除命令": 按唯一键匹配在途报警并消除。

        匹配字段由 alarm_clear_match_fields 配置 (默认四要素)。匹配不到 → alarm_not_found
        (川南 v4 约定 40007)。复用 field_map 把外部字段映射成我们的字段。
        """
        if not cfg.get("enabled"):
            return self._result(cfg, "disabled", "入站对接未启用")

        mapped = self._map_fields(body, cfg.get("field_map") or {})

        from backend.services.external_alarm import clear_alarms
        match_fields = cfg.get("alarm_clear_match_fields") or None
        debug_center.dbg("backend.mes", "收到入站报警消除",
                         f"匹配字段={match_fields or '默认四要素'} "
                         f"取值={ {k: mapped.get(k) for k in (match_fields or [])} if match_fields else mapped}")
        try:
            res = clear_alarms(db, mapped, match_fields=match_fields, clear_source="external")
        except Exception as e:
            debug_center.dbg("backend.mes", "报警消除处理异常", f"mapped={mapped} err={e}")
            return self._result(cfg, "internal_error", f"内部处理异常: {e}", mapped=mapped)

        if not res.get("matched"):
            debug_center.dbg("backend.mes", "报警消除未命中", "按匹配字段未找到在途报警, 回 alarm_not_found")
            return self._result(cfg, "alarm_not_found",
                                "未找到对应报警记录，无法消除", mapped=mapped)

        debug_center.dbg("backend.mes", "报警消除命中", f"已消除 {res.get('cleared', 0)} 条在途报警")

        success_msg = (cfg.get("response") or {}).get("success_message") or "OK"
        return self._result(cfg, "success",
                            f"{success_msg} (已消除 {res.get('cleared', 0)} 条报警)",
                            mapped=mapped, success=True)

    def _apply_task_action(self, db, mapped: dict, cfg: dict):
        """开工任务的实际处理 (编排)。返回 (ok, error_key, message)。

        三块均按配置开关 opt-in, 默认全关 (开箱即用只接收登记不动运行态):
          - 完工信号优先: complete_field 命中真值 → 收工单 (不再当开工处理)
          - switch_project_on_task: 按产品代号切检测项目 (复用 activate_project_core)
          - create_work_order_on_task: 建/激活工单 (order_no=task_no), 让周期绑该任务
        """
        complete_field = cfg.get("complete_field")
        if complete_field and self._truthy(mapped.get(complete_field),
                                           cfg.get("complete_true_words")):
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

        if cfg.get("start_detection_on_task", False):
            # 先提交本次事务再拉检测: start_detection 内部会另开 DB 会话写检测记录,
            # 与本请求未提交的工单写事务互斥, 不先 commit 会撞满 SQLite busy_timeout
            # (15s), 上游中控超时短于它就会误判"连接失败" (2026-07-13 UAT 逼出的真锁)
            if db is not None:
                try:
                    db.commit()
                except Exception:
                    pass
            started, skipped = self._auto_start_detection()
            if started:
                msgs.append(f"已自动开始检测: {', '.join(started)}")
            # v3.39 川南反馈: 开关开了却没拉起时客户只看到 ok, 完全无从排查
            # (原因只进调试日志)。把没拉起的原因直接带回开工响应, 一次请求即可自诊。
            if skipped:
                msgs.append(f"自动开始检测未执行({'; '.join(skipped)})")

        # v3.38 川南反馈: 检测已在跑时收到开工, 新单挂不上运行中的工位
        # (监控页四要素不显示、周期不计入新任务, 要停一次检测才生效)。
        # 建单成功后把新单回填给有活跃会话的工位: 空位无条件补挂;
        # 已挂旧单仅在"最新开工为准"开启时顶替 (复用已有语义, 不新增开关)。
        if cfg.get("create_work_order_on_task", False):
            if db is not None:
                try:
                    db.commit()  # 回填在 hook 工作线程另开会话查单, 必须先落盘
                except Exception:
                    pass
            self._rebind_running_channels(cfg)

        return (True, None, "; ".join(msgs) if msgs else "ok")

    def _rebind_running_channels(self, cfg: dict):
        """把最新在产单回填到"检测会话正在跑"的工位 (v3.38)。

        只投递内存绑定任务给 MES hook 工作线程, 不在入站请求里做任何 DB 写;
        任何失败只记调试日志, 绝不影响开工响应。刚被自动拉起检测的工位会走
        session_start 原生绑定, 这里的回填与之幂等 (同一单不重复动作)。
        """
        try:
            from backend.api.channel_manager import channel_manager
            from backend.services.mes_hooks import get_mes_hook
            hook = get_mes_hook()
            if not hook.enabled:
                return
            allow_replace = bool(cfg.get("supersede_previous_task", False))
            for ch_id, mgr in list(channel_manager.channels.items()):
                try:
                    session_id = getattr(mgr, 'current_session_id', None)
                    if not mgr or not getattr(mgr, 'is_detecting', False) or not session_id:
                        continue
                    project_id = (mgr.project_config or {}).get('id')
                    if not project_id:
                        continue
                    hook.on_external_order_changed(
                        ch_id, session_id, project_id, allow_replace=allow_replace)
                except Exception as e:
                    debug_center.dbg("backend.mes", "活跃工单回填投递失败",
                                     f"ch{ch_id} err={e}")
        except Exception as e:
            debug_center.dbg("backend.mes", "活跃工单回填失败", f"整体异常: {e}")

    def _auto_start_detection(self) -> tuple:
        """开工后自动拉起检测 (start_detection_on_task 开时)。

        门槛: 配置过视频源 (暂停中则借 start_detection 的复活路径重新拉起,
        与手动点"开始"行为完全一致) + 模型就绪 + 未在检测。
        任何失败只记调试日志, 绝不影响开工响应 (任务本身已处理成功)。
        返回 (started, skipped): started 为本次拉起的工位列表 (如 ["ch0"]);
        skipped 为没拉起的工位及原因 (已在检测中的不算, 那是正常态)——
        v3.39 起原因随开工响应回给上游, 现场不开调试日志也能一眼看到卡在哪一关。
        """
        started = []
        skipped = []
        try:
            from backend.api.channel_manager import channel_manager
            for ch_id, mgr in list(channel_manager.channels.items()):
                try:
                    if not mgr:
                        continue
                    # 川南现场事故链 (2026-07-15 日志钉死): 点过"停止"后视频源暂停,
                    # 老逻辑在这里因"源未运行"直接放弃, 之后每次开工都拉不起来。
                    # 而 start_detection 本就有"暂停续播/相机重连"的复活路径 (手动点
                    # "开始"走的就是它), 所以只要配置过视频源就放行交给它拉起;
                    # 只有"本次启动从没配置过源"才真正无从下手。
                    if not mgr.is_running and not getattr(mgr, 'source_type', None):
                        skipped.append(f"ch{ch_id}: 未配置视频源")
                        debug_center.dbg("backend.mes", "开工自动开始检测跳过",
                                         f"ch{ch_id} 未配置视频源")
                        continue
                    if mgr.is_detecting:
                        continue
                    # 川南现场高频卡点: 手动点"开始检测"时前端会把项目里的模型清单一起传来,
                    # 自动拉起没有这个来源, 只能用通道上已加载的模型 (来自项目"默认模型")。
                    # 项目没配默认模型 → 这里必然失败, 给出能直接指路的原因。
                    if getattr(mgr, 'model', None) is None \
                            and getattr(mgr, 'source_type', None) != 'synthetic':
                        skipped.append(f"ch{ch_id}: 模型未就绪(请在项目管理为该项目配置默认模型)")
                        debug_center.dbg("backend.mes", "开工自动开始检测跳过",
                                         f"ch{ch_id} 模型未就绪 (项目未配默认模型?)")
                        continue
                    mgr.start_detection()
                    started.append(f"ch{ch_id}")
                    debug_center.dbg("backend.mes", "开工自动开始检测",
                                     f"ch{ch_id} 已随开工任务拉起检测")
                except Exception as e:
                    skipped.append(f"ch{ch_id}: {e}")
                    debug_center.dbg("backend.mes", "开工自动开始检测失败",
                                     f"ch{ch_id} err={e}")
        except Exception as e:
            debug_center.dbg("backend.mes", "开工自动开始检测失败", f"整体异常: {e}")
        return started, skipped

    def _switch_project(self, db, mapped: dict, cfg: dict):
        """按产品代号切检测项目, 复用 projects.activate_project_core (与手动激活一致)。"""
        # 产品代号无匹配时的返回文案 (川南 v4 第 1 条要求固定话术)
        unknown_msg = cfg.get("unknown_product_message") or "未查询到当前产品代号检测模型"
        product_code = str(mapped.get("product_code") or "").strip()
        if not product_code:
            return (False, "unknown_product", "缺少产品代号, 无法匹配检测项目")

        from backend.models.models import Project
        from backend.services.project_match import resolve_project_id_by_spec

        # 取项目口径见 services.project_match.resolve_project_id_by_spec
        # (对照表精确 → 通配符 → 自动同名子串, 与上银包装线完全一致)。
        project_id, hit_by = resolve_project_id_by_spec(
            db, product_code, cfg.get("product_project_map") or {},
            match_by_name=cfg.get("match_project_by_name", False),
            strict_boundary=cfg.get("name_match_strict_boundary", False))
        if project_id is None:
            debug_center.dbg("backend.mes", "切项目未命中产品码",
                             f"product={product_code} 对照表/同名项目均无 → 回 unknown_product")
            return (False, "unknown_product", unknown_msg)
        debug_center.dbg("backend.mes", "切项目命中",
                         f"product={product_code} → 项目#{project_id} (经{hit_by})")
        try:
            project_id = int(project_id)
        except Exception:
            return (False, "unknown_product", unknown_msg)

        # 已经是激活项目 → 跳过重载, 避免无谓切模型打断正在跑的产线
        active = db.query(Project).filter(Project.is_active == True).first()
        if active and active.id == project_id:
            return (True, None, f"产品 {product_code} 对应项目#{project_id} 已激活")

        try:
            from backend.api.projects import activate_project_core
            activate_project_core(db, project_id)
        except Exception as e:
            # activate_project_core 项目不存在抛 HTTPException(404)
            if getattr(e, "status_code", None) == 404:
                return (False, "unknown_product", unknown_msg)
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
            debug_center.dbg("backend.mes", "建工单+四要素留痕",
                             f"task_no={task_no} product={mapped.get('product_code')} "
                             f"step={mapped.get('step_code')} operator={mapped.get('operator')} "
                             f"绑定={data.get('binding_scope')}")
        else:
            # v3.40 川南现场钉死的 bug: 同任务号重复开工走复用分支, 但工单上的项目
            # 绑定停留在"第一次创建时"的项目 (甚至为空)。工位挂单按"工单绑定项目 ==
            # 工位当前项目"匹配 → 绑定过期的复用单永远挂不上 → 监控页四要素不显示、
            # 出站推送里工单字段全 null。复用时以本次开工为准刷新绑定与四要素留痕。
            self._refresh_reused_order(db, order, mapped, cfg)
            debug_center.dbg("backend.mes", "复用已有工单",
                             f"task_no={task_no} status={order.status} "
                             f"scope={order.binding_scope} project={order.project_id}")

        self._ensure_in_progress(svc, db, order)

        # 最新开工为准: 顶替当前在产的其它外部任务 (并对其回传完工)
        superseded = 0
        if cfg.get("supersede_previous_task", False):
            superseded = self._supersede_previous_tasks(db, order, cfg)
            if superseded:
                debug_center.dbg("backend.mes", "最新开工顶替旧任务",
                                 f"new={order.order_no} 顶替并回推完工 {superseded} 个")

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

    def _refresh_reused_order(self, db, order, mapped: dict, cfg: dict):
        """复用工单时按本次开工报文刷新绑定与四要素 (v3.40 川南修复)。

        原则"最新开工为准":
          - 项目路由 → 重绑当前激活项目 (工单第一次创建时的项目绑定可能已过期/为空,
            不刷新则工位挂单匹配永远落空)
          - 工位路由 → 重绑本次报文里的工位号
          - 四要素留痕 (extra_data.inbound) 与产品码/操作员同步覆盖为本次报文值,
            否则同任务号第二次开工换了操作员/工步, 界面与推送仍显示旧值
        终态单 (completed/cancelled) 不在此处理 — 由 _ensure_in_progress 决定是否复活。
        任何失败只记日志, 不阻断开工接收。
        """
        try:
            from backend.models.models import Project
            if (cfg.get("order_binding") or "project").lower() == "channel":
                ch = self._parse_channel(mapped.get(cfg.get("channel_field") or "channel"))
                if ch is not None:
                    order.binding_scope = "channels"
                    order.target_channels = [ch]
            else:
                active = db.query(Project).filter(Project.is_active == True).first()
                if active is not None:
                    order.binding_scope = "project"
                    order.project_id = active.id
            if cfg.get("store_mapped_extra", True):
                # JSON 列整体重赋值 (原地改 dict 不触发 SQLAlchemy 变更追踪)
                extra = dict(order.extra_data or {})
                extra["inbound"] = dict(mapped)
                order.extra_data = extra
            if mapped.get("product_code"):
                order.product_code = mapped.get("product_code")
            if mapped.get("operator"):
                order.created_by = mapped.get("operator")
            db.flush()
        except Exception as e:
            debug_center.dbg("backend.mes", "复用工单刷新绑定失败",
                             f"order={getattr(order, 'order_no', '?')} err={e}")

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
    def _truthy(v, words=None) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        # 非零数字恒为真 (兼容 1/1.0)
        if isinstance(v, (int, float)):
            return v != 0
        if words:
            table = {str(w).strip().lower() for w in words if str(w).strip()}
        else:
            table = DEFAULT_TRUE_WORDS
        return str(v).strip().lower() in table

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

        # 固定文案覆盖: 配了 messages[code_key] 就用客户文案 (替代代码内置默认)
        override = (rc.get("messages") or {}).get(code_key)
        if override is not None and str(override) != "":
            message = str(override)

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
