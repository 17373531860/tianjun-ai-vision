"""
外部 MES 工单主动拉取服务 (v3.20)

与 mes_gateway (出站推送) 方向相反: 我们主动调外部 MES 的查询接口, 把工单拉回来落库。
完全配置驱动, 复用 MESConnection 表 + config.pull JSON 子树, 零客户特异代码
(上银 HIWIN = 填一份配置, 下一个 MES 客户 = 填另一份配置)。

拉取流程:
  request_body_template (含 {job_no} 占位) → HTTP 请求 (复用网关鉴权封装)
  → 成功判定 (success_path == success_value)
  → 取数组 (array_path) → 逐条字段映射 (field_mapping)
  → 按 import_mode upsert 工单 (复用 WorkOrderService)
  → 写 MESCommLog (direction='pull')

config.pull 结构 (全部存 MESConnection.config['pull'], 无新表):
{
  "enabled": true,
  "url": "https://itweb.hiwin.cn/java_demo_test/api",
  "method": "POST",
  "content_type": "application/json; charset=UTF-8",
  "request_body_template": "{\"api\":\"...\",\"parameters\":{\"job_no\":\"{job_no}\"}}",
  "success_path": "statusCode",          # 哪个字段判成功 (点路径; 上银也可用 success/true)
  "success_value": 200,                  # 等于什么算成功
  "array_path": "response.resultData",   # 工单数组埋在哪 (点路径; 上银是两层嵌套!)
  "field_mapping": {                     # 我们的工单字段 ← 外部字段 (点路径)
      "order_no": "job_no", "customer_name": "cust_name",
      "product_spec": "spec", "planned_qty": "dispatch_qty",
      "product_name": "spec"
  },
  "import_mode": "upsert",               # validate / upsert / upsert_with_planned
  "timeout_sec": 10, "retry_count": 1, "retry_interval_sec": 2,
  "verify_ssl": true,
  "auth": {...}, "custom_headers": [...],# 复用 MESGateway._apply_auth_to_headers
  "triggers": {"manual": true, "scheduled": false, "interval_sec": 300, "on_scan": false}
}
"""
import json
import threading
import time
import traceback
from datetime import datetime
from typing import Optional

import requests

from backend.db.database import SessionLocal
from backend.models.mes_models import MESConnection, MESCommLog
from backend.services.mes_adapters.base import _get_nested
from backend.services.mes_gateway import MESGateway
from backend.services.work_order import WorkOrderService
from backend.core import debug_center


# 上银 HIWIN 预设映射 (前端"上银"模板一键填充用; 主程序不依赖它做任何分支判断)
DEFAULT_FIELD_MAPPING = {
    "order_no": "job_no",
    "customer_name": "cust_name",
    "product_spec": "spec",
    "planned_qty": "dispatch_qty",
    "product_name": "spec",
}


class MESPuller:
    """配置驱动的外部 MES 工单拉取器。无状态, 可单例复用。"""

    def __init__(self):
        self.wo_svc = WorkOrderService()

    # ============================================================
    # 对外主入口
    # ============================================================
    def pull_for_connection(self, db, conn: MESConnection, *,
                            job_no: str = "", dry_run: bool = False,
                            max_items: Optional[int] = None) -> dict:
        """按某条连接的 config.pull 执行一次拉取, 含写通讯日志 + 更新 last_sync_at。

        dry_run=True: 解析+映射但不落库 (给"试同步看看"用)。
        """
        pull_cfg = ((conn.config or {}).get("pull")) or {}
        if not pull_cfg.get("url"):
            return {"success": False, "error": "未配置拉取地址 (config.pull.url)"}

        result = self._pull_with_config(db, pull_cfg, job_no=job_no,
                                        dry_run=dry_run, max_items=max_items)

        # 写一条拉取通讯日志 (复用推送的日志表, direction='pull')
        try:
            log = MESCommLog(
                connection_id=conn.id,
                direction="pull",
                event_type="order_query" + ("_dryrun" if dry_run else ""),
                method=pull_cfg.get("method", "POST"),
                url=pull_cfg.get("url"),
                request_body=result.get("_request_body"),
                response_body=result.get("_response_snippet"),
                status_code=result.get("http_status"),
                success=result.get("success", False),
                error_msg=result.get("error"),
                duration_ms=result.get("duration_ms"),
            )
            db.add(log)
            if result.get("success") and not dry_run:
                conn.last_sync_at = datetime.now()
            db.flush()
            result["log_id"] = log.id
        except Exception as e:
            debug_center.dbg("backend.gateway", "拉取日志写入失败", str(e))

        # 内部字段不外泄给 API 响应
        result.pop("_request_body", None)
        result.pop("_response_snippet", None)
        return result

    def test_connection(self, pull_cfg: dict, *, job_no: str = "") -> dict:
        """只发一次请求并自动识别返回结构, 不映射不落库 (给前端"测试连接")。

        返回里带 structure_guess: 自动猜出的数组路径 + 字段候选, 供前端做点选映射。
        """
        if not pull_cfg.get("url"):
            return {"success": False, "error": "请先填写拉取地址"}
        body_str, payload = self._render_body(pull_cfg, job_no)
        http = self._http_request(pull_cfg, payload)
        out = {
            "success": http["error"] is None and http["status_code"] in (200, 201),
            "http_status": http["status_code"],
            "error": http["error"],
            "duration_ms": http["duration_ms"],
            "raw_body": http["body"],
        }
        if isinstance(http["body"], (dict, list)):
            out["structure_guess"] = self._guess_structure(http["body"])
        return out

    # ============================================================
    # 核心流程 (纯逻辑, 便于单测)
    # ============================================================
    def _pull_with_config(self, db, pull_cfg: dict, *, job_no: str = "",
                          dry_run: bool = False,
                          max_items: Optional[int] = None) -> dict:
        body_str, payload = self._render_body(pull_cfg, job_no)
        http = self._http_request(pull_cfg, payload)
        debug_center.dbg("backend.pull", "发起拉取",
                         f"job_no={job_no or '(全部)'} url={pull_cfg.get('url', '')} "
                         f"dry_run={dry_run} → HTTP {http['status_code']} ({http['duration_ms']}ms)")

        result = {
            "success": False,
            "error": None,
            "http_status": http["status_code"],
            "duration_ms": http["duration_ms"],
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "validated": 0,
            "skipped": 0,
            "items": [],
            "_request_body": body_str,
            "_response_snippet": self._snippet(http["body"]),
        }

        if http["error"]:
            result["error"] = http["error"]
            debug_center.dbg("backend.pull", "拉取失败(HTTP/网络)", str(http["error"]))
            return result

        body = http["body"]
        if not self._is_success(body, pull_cfg):
            sp = pull_cfg.get("success_path") or "statusCode"
            result["error"] = (
                f"外部 MES 返回非成功 ({sp}={_get_nested(body, sp) if isinstance(body, dict) else 'N/A'})"
            )
            # 把外部的错误描述也带回来, 方便排障。上银把详情嵌在 error 对象里
            # (error.errorInfo / error.detail_message), 也兼容别家放顶层的情况。
            err_sources = []
            if isinstance(body, dict):
                err_sources.append(body)
                if isinstance(body.get("error"), dict):
                    err_sources.append(body["error"])
            for src in err_sources:
                hit = next((src[k] for k in
                            ("errorInfo", "detail_message", "message", "msg", "errorMsg")
                            if src.get(k)), None)
                if hit:
                    result["error"] += f": {hit}"
                    break
            debug_center.dbg("backend.pull", "拉取失败(返回非成功)", result["error"])
            return result

        rows = self._extract_array(body, pull_cfg.get("array_path") or "")
        if max_items:
            rows = rows[:max_items]
        result["fetched"] = len(rows)

        mapping = pull_cfg.get("field_mapping") or DEFAULT_FIELD_MAPPING
        import_mode = (pull_cfg.get("import_mode") or "upsert").lower()

        for row in rows:
            mapped = self._map_item(row, mapping)
            action, order_no = self._upsert_order(db, mapped, import_mode, dry_run)
            if action == "created":
                result["created"] += 1
            elif action == "updated":
                result["updated"] += 1
            elif action in ("matched", "remote_only"):
                result["validated"] += 1
            else:
                result["skipped"] += 1
            result["items"].append({
                "order_no": order_no,
                "mapped": mapped,
                "action": action,
            })

        if not dry_run:
            db.flush()
        result["success"] = True
        debug_center.dbg("backend.pull", "拉取完成",
                         f"取回 {result['fetched']} 条: 新建 {result['created']} / 更新 {result['updated']} "
                         f"/ 校验 {result['validated']} / 跳过 {result['skipped']} (dry_run={dry_run})")
        return result

    # ============================================================
    # 子步骤
    # ============================================================
    def _render_body(self, pull_cfg: dict, job_no: str):
        """渲染请求体模板, 把 {job_no} 等占位符替换成实际值。

        返回 (原始字符串, 解析成 dict/原样 的 payload)。
        """
        tpl = pull_cfg.get("request_body_template")
        variables = {"job_no": job_no or ""}
        if tpl is None:
            # 没给模板就发个最朴素的 {job_no}
            payload = {"job_no": job_no or ""}
            return json.dumps(payload, ensure_ascii=False), payload
        if isinstance(tpl, dict):
            tpl = json.dumps(tpl, ensure_ascii=False)
        rendered = str(tpl)
        for k, v in variables.items():
            rendered = rendered.replace("{" + k + "}", str(v))
        try:
            payload = json.loads(rendered)
        except Exception:
            payload = rendered  # 非 JSON 模板原样发 (适配少数怪接口)
        return rendered, payload

    def _http_request(self, pull_cfg: dict, payload) -> dict:
        """发请求 (含重试 + 复用网关鉴权)。返回 {status_code, body, error, duration_ms}。"""
        url = pull_cfg.get("url", "")
        method = (pull_cfg.get("method") or "POST").upper()
        timeout = pull_cfg.get("timeout_sec", 10)
        verify = pull_cfg.get("verify_ssl", True)
        retry_count = pull_cfg.get("retry_count", 1) or 0
        retry_interval = pull_cfg.get("retry_interval_sec", 2) or 0
        # 默认绕过系统代理: 工控机 / 开发机常开 clash 等全局代理, 会把发往
        # MES 内网地址(如 10.x.x.x)的请求劫持到外网导致连不上。MES 对接绝大多数是
        # 内网直连, 故默认 proxies 显式置空(覆盖环境变量代理)。确需走代理的可在
        # 配置里设 use_proxy=true 恢复"跟随系统代理"。
        use_proxy = bool(pull_cfg.get("use_proxy", False))
        proxies = None if use_proxy else {"http": None, "https": None}

        # 鉴权: 复用推送网关那套 (bearer/api_key/custom_header → headers)
        eff = MESGateway._apply_auth_to_headers(pull_cfg)
        headers = dict(eff.get("headers") or {})
        if "Content-Type" not in headers:
            headers["Content-Type"] = pull_cfg.get("content_type") or "application/json"
        auth = self._build_basic_auth(pull_cfg.get("auth"))

        is_json = isinstance(payload, (dict, list))
        last = {"status_code": 0, "body": None, "error": "未发起请求", "duration_ms": 0}

        for attempt in range(1 + retry_count):
            if attempt > 0 and retry_interval:
                time.sleep(retry_interval)
            start = time.time()
            try:
                resp = requests.request(
                    method, url,
                    json=payload if (is_json and method in ("POST", "PUT", "PATCH")) else None,
                    data=None if is_json else payload,
                    params=payload if (is_json and method == "GET") else None,
                    headers=headers, auth=auth, timeout=timeout, verify=verify,
                    proxies=proxies,
                )
                elapsed = int((time.time() - start) * 1000)
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text
                last = {"status_code": resp.status_code, "body": body,
                        "error": None, "duration_ms": elapsed}
                if resp.status_code in (200, 201):
                    return last
                # 非 2xx: 把返回体里的错误详情拼进 error, 方便现场排查。
                # 外部 MES 常把真实原因写在 body (上银是 error.errorInfo), 只回 "HTTP 500"
                # 现场无从下手。通用探测常见错误字段, 不硬编码任何厂商。
                hint = ""
                if isinstance(body, dict):
                    eo = body.get("error")
                    if isinstance(eo, dict):
                        hint = eo.get("errorInfo") or eo.get("message") or eo.get("msg") or ""
                    elif isinstance(eo, str):
                        hint = eo
                    hint = hint or body.get("message") or body.get("msg") or body.get("detail") or ""
                elif isinstance(body, str):
                    hint = body[:160]
                last["error"] = f"HTTP {resp.status_code}" + (f": {hint}" if hint else "")
            except requests.Timeout:
                last = {"status_code": 0, "body": None,
                        "error": f"请求超时 ({timeout}s)",
                        "duration_ms": int((time.time() - start) * 1000)}
            except requests.ConnectionError as e:
                last = {"status_code": 0, "body": None,
                        "error": f"连接失败: {e}",
                        "duration_ms": int((time.time() - start) * 1000)}
            except Exception as e:
                last = {"status_code": 0, "body": None, "error": str(e),
                        "duration_ms": int((time.time() - start) * 1000)}
        return last

    @staticmethod
    def _build_basic_auth(auth_config):
        if isinstance(auth_config, dict) and auth_config.get("type") == "basic":
            return (auth_config.get("username", ""), auth_config.get("password", ""))
        return None

    @staticmethod
    def _is_success(body, pull_cfg: dict) -> bool:
        """成功判定: success_path 字段 == success_value。没配则看 HTTP 状态码 (已在前面过)。"""
        sp = pull_cfg.get("success_path")
        if not sp:
            return True  # 没配成功判定 = 只要 HTTP 200 就算成功
        if not isinstance(body, dict):
            return False
        expect = pull_cfg.get("success_value", 200)
        actual = _get_nested(body, sp)
        # 容忍字符串/数字差异 (statusCode 可能是 "200" 或 200)
        return str(actual) == str(expect)

    @staticmethod
    def _extract_array(body, array_path: str) -> list:
        """从返回里取工单数组。array_path 为空时: body 本身是数组就用它, 否则空。"""
        if not array_path:
            return body if isinstance(body, list) else []
        arr = _get_nested(body, array_path) if isinstance(body, dict) else None
        if isinstance(arr, list):
            return arr
        if isinstance(arr, dict):
            return [arr]  # 单条对象也包成列表
        return []

    @staticmethod
    def _map_item(item: dict, mapping: dict) -> dict:
        """外部一条记录 → 我们的工单字段。mapping = {我们字段: 外部字段点路径}。"""
        out = {}
        if not isinstance(item, dict):
            return out
        for our_field, ext_path in (mapping or {}).items():
            if not ext_path:
                continue
            val = _get_nested(item, ext_path)
            if val is not None:
                out[our_field] = val
        return out

    def _upsert_order(self, db, mapped: dict, import_mode: str, dry_run: bool):
        """按 import_mode 落库一条工单。返回 (action, order_no)。

        action: created / updated / matched / remote_only / skipped
        """
        order_no = str(mapped.get("order_no") or "").strip()
        if not order_no:
            return ("skipped", "")

        existing = self.wo_svc.get_order_by_no(db, order_no)

        # 仅校验模式: 不写库, 只报告本地有没有
        if import_mode == "validate":
            return ("matched" if existing else "remote_only", order_no)

        force_planned = import_mode == "upsert_with_planned"

        if existing:
            if dry_run:
                return ("updated", order_no)
            if mapped.get("customer_name") is not None:
                existing.customer_name = str(mapped["customer_name"])
            if mapped.get("product_spec") is not None:
                existing.product_spec = str(mapped["product_spec"])
            if mapped.get("product_name") is not None:
                existing.product_name = str(mapped["product_name"])
            if force_planned and mapped.get("planned_qty") is not None:
                existing.planned_qty = self._to_int(mapped["planned_qty"])
            return ("updated", order_no)

        if dry_run:
            return ("created", order_no)

        # product_name 必填: 外部没有产品名时用规格 / 工单号兜底
        product_name = (mapped.get("product_name")
                        or mapped.get("product_spec") or order_no)
        data = {
            "order_no": order_no,
            "product_name": str(product_name),
            "product_spec": (str(mapped["product_spec"])
                             if mapped.get("product_spec") is not None else None),
            "customer_name": (str(mapped["customer_name"])
                              if mapped.get("customer_name") is not None else None),
            "planned_qty": self._to_int(mapped.get("planned_qty", 0)),
            "external_id": order_no,
            "source": "external",
            "status": "pending",
            "binding_scope": "project",  # strict=False (source=external) 允许不绑项目入库
        }
        self.wo_svc.create_order(db, data)
        return ("created", order_no)

    @staticmethod
    def _to_int(v) -> int:
        try:
            return int(float(v))
        except Exception:
            return 0

    # ============================================================
    # 结构自动识别 (给前端测试驱动映射)
    # ============================================================
    @staticmethod
    def _find_object_array(obj, prefix: str = "", depth: int = 0):
        """深度优先找第一个'非空对象数组', 返回 (点路径, 数组)。

        上银把工单数组埋在 response.resultData (两层), 所以不能只扫顶层 ——
        本层找不到就下探子对象, 路径用点号拼接 (如 'response.resultData')。
        优先常见命名 (resultData/data/list...), 最多下探 4 层防深递归。
        """
        if depth > 4 or not isinstance(obj, dict):
            return None
        preferred = ["resultData", "data", "list", "rows", "items", "records"]
        keys = [k for k in preferred if k in obj] + [k for k in obj if k not in preferred]
        # 1) 本层直接命中数组
        for k in keys:
            v = obj.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return (f"{prefix}{k}", v)
        # 2) 下探子对象 (如 response.resultData)
        for k in keys:
            v = obj.get(k)
            if isinstance(v, dict):
                found = MESPuller._find_object_array(v, f"{prefix}{k}.", depth + 1)
                if found:
                    return found
        return None

    def _guess_structure(self, body) -> dict:
        """从返回里自动猜 数组路径 + 字段候选, 让小白点一下就完成映射。"""
        if isinstance(body, list):
            sample = body[0] if body and isinstance(body[0], dict) else {}
            return {"array_path": "", "fields": list(sample.keys()), "sample": sample}
        if isinstance(body, dict):
            found = self._find_object_array(body)
            if found:
                path, arr = found
                return {"array_path": path, "fields": list(arr[0].keys()), "sample": arr[0]}
            # 没找到数组, 返回顶层键供人工选
            return {"array_path": "", "fields": list(body.keys()), "sample": body}
        return {"array_path": "", "fields": [], "sample": None}

    @staticmethod
    def _snippet(body, limit: int = 2000) -> Optional[str]:
        if body is None:
            return None
        try:
            s = json.dumps(body, ensure_ascii=False, default=str) if isinstance(body, (dict, list)) else str(body)
        except Exception:
            s = str(body)
        return s[:limit]


_puller_instance: Optional[MESPuller] = None


def get_mes_puller() -> MESPuller:
    global _puller_instance
    if _puller_instance is None:
        _puller_instance = MESPuller()
    return _puller_instance


# ============================================================
# 定时拉取调度器 (后台线程, 按各连接 config.pull.triggers.interval_sec 周期拉取)
# ============================================================
class PullScheduler:
    """单后台线程轮询: 每 TICK_SEC 扫一遍连接, 到点的执行一次拉取。

    设计要点:
      - 错误隔离: 单条连接拉取失败不影响其他连接, 也不崩线程
      - 不依赖系统定时器, 用 _last_run 时间戳 + interval 判断到点
      - daemon 线程, 进程退出自动结束 (与项目现有清理线程一致)
      - 启动即先拉一次 (last_run=0), 满足客户"开机就同步当班工单"
    """
    TICK_SEC = 15

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_run: dict[int, float] = {}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mes-pull-scheduler")
        self._thread.start()
        print("[MES-Pull] scheduled pull scheduler started", flush=True)

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                debug_center.dbg("backend.gateway", "拉取调度 tick 异常", str(e))
            self._stop.wait(self.TICK_SEC)

    def _tick(self):
        db = SessionLocal()
        try:
            conns = db.query(MESConnection).all()
        except Exception:
            db.close()
            return
        now = time.time()
        puller = get_mes_puller()
        for conn in conns:
            try:
                pull = (conn.config or {}).get("pull") or {}
                trig = pull.get("triggers") or {}
                if not getattr(conn, "pull_enabled", False):
                    continue
                if not trig.get("scheduled"):
                    continue
                if not pull.get("url"):
                    continue
                interval = int(trig.get("interval_sec")
                               or getattr(conn, "pull_interval_sec", 0) or 300)
                interval = max(10, interval)
                if now - self._last_run.get(conn.id, 0) < interval:
                    continue
                self._last_run[conn.id] = now
                puller.pull_for_connection(db, conn, job_no="", dry_run=False)
                db.commit()
                print(f"[MES-Pull] scheduled pull done: {conn.name}", flush=True)
            except Exception as e:
                db.rollback()
                debug_center.dbg("backend.gateway", "定时拉取单连接失败",
                                 f"conn={getattr(conn, 'id', None)} err={e}")
        db.close()


_scheduler_instance: Optional[PullScheduler] = None


def get_pull_scheduler() -> PullScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = PullScheduler()
    return _scheduler_instance
