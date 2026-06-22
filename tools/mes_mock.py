"""本地 MES mock 服务 — 上银包装线 MES 对接闭环开发/测试用 (v3.22).

为什么有它:
  上银真 MES 只允许在客户工控机连, 开发机连不上. 这个 mock 模拟"扫工单号 →
  查 MES → 拿滑块总数 + 产品规格"那一跳, 让整条包装结算闭环 (尾箱算法 / 自动切
  项目 / 塞工单 gate) 能在开发机端到端跑通.

接法 (两套返回格式任选, 对应不同 pull_cfg):
  A. 简易格式 (旧, 给在线 e2e 单测用):
      url=http://127.0.0.1:9100/mes/work-order  array_path=data  success_path=code success_value=0
  B. 上银真实格式 (推荐, 与客户工控机部署完全一致):
      url=http://127.0.0.1:9100/hiwin/webcn/ai_error_prevention_job_info/query
      method=POST  request_body_template={"api":"...","parameters":{"job_no":"{job_no}"}}
      success_path=statusCode  success_value=200  array_path=response.resultData
      字段 dispatch_qty (滑块总数) / spec (产品规格)
  上银工控机部署时换成真 MES 地址即可, 主程序代码零改动.

启动:
      python tools/mes_mock.py                 # 0.0.0.0:9100
      MES_MOCK_PORT=9101 python tools/mes_mock.py

覆盖的测试场景 (按工单号命中, 去连字符大小写不敏感):
      ORD-NONEXACT  非整除尾箱   250 滑块 / 规格 HGH20  (每箱96 → 3箱, 尾箱58)
      ORD-EXACT     整除尾箱     240 滑块 / 规格 HGW15  (每箱24 → 10箱, 尾箱满24)
      ORD-SINGLE    单箱         50  滑块 / 规格 HGH20  (每箱96 → 1箱, 尾箱50)
      ORD-SPEC2     多型号       192 滑块 / 规格 EGH15  (自动切另一项目, 每箱64 → 3箱整除)
      JOB-ERR       API 错误     上银格式返回 statusCode=500 + error (模拟系统异常)
      其它工单号    查不到 (statusCode=200 + resultData 空) → 触发 on_mes_fail 策略测试
"""
import os
from typing import Any, Dict, List

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError as e:  # pragma: no cover
    raise SystemExit(f"缺少依赖 fastapi/uvicorn, 在 tianjun 环境跑: {e}")


app = FastAPI(title="MES Mock (上银包装线)")

# 内置工单 → {客户名, 滑块总数(排产量), 产品规格}. 覆盖整除/非整除/单箱/多型号.
#
# 键 = 规范工单号 (带连字符), 与"扫码去 - → 软件补 - 后"的查询值一致.
#   扫码枪读到 JOB1503000213 (无 -) → 软件第 12 位后补 - → JOB150300021-3 → 查 MES.
#   _norm 会去 - 大小写不敏感, 所以带不带 - 都能命中, 真实流程是带 - 来查.
#
# 上银 SY 九条工单 (规格统一 SY = 96 滑块/箱, 公司各异, 排产量各异):
#   覆盖刚好满箱 / 带尾箱 / 多装(排产<实检) / 整数倍多箱 等组合.
ORDERS: Dict[str, Dict[str, Any]] = {
    # --- 上银 SY 九条 (UAT 主场景) ---
    "JOB150300021-3":   {"cust_name": "上银科技(苏州)有限公司",   "dispatch_qty": 96,  "spec": "SY"},  # 刚好 1 箱满
    "JOB150300021-31":  {"cust_name": "上银精密机械(嘉兴)有限公司", "dispatch_qty": 100, "spec": "SY"},  # 1 满箱 + 尾箱 4
    "JOB150300021-313": {"cust_name": "大银微系统股份有限公司",     "dispatch_qty": 48,  "spec": "SY"},  # 排产 48 但实检 96 → 多装
    "JOB202605132-1":   {"cust_name": "台湾上银科技股份有限公司",   "dispatch_qty": 192, "spec": "SY"},  # 整 2 箱
    "JOB202605132-12":  {"cust_name": "上银智能装备(常州)有限公司", "dispatch_qty": 200, "spec": "SY"},  # 2 满箱 + 尾箱 8
    "JOB202605132-123": {"cust_name": "广东上银传动科技有限公司",   "dispatch_qty": 96,  "spec": "SY"},  # 刚好 1 箱满 (另一客户)
    "JOB202406221-2":   {"cust_name": "上银光电(深圳)有限公司",     "dispatch_qty": 288, "spec": "SY"},  # 整 3 箱
    "JOB202406221-21":  {"cust_name": "上银自动化(东莞)有限公司",   "dispatch_qty": 50,  "spec": "SY"},  # 单箱尾箱 50
    "JOB202406221-213": {"cust_name": "上银机器人(上海)有限公司",   "dispatch_qty": 96,  "spec": "SY"},  # 刚好 1 箱满
    # --- 历史测试单 (保留, 勿删: 在线 e2e 单测依赖) ---
    "ORD-NONEXACT": {"cust_name": "通用客户A", "dispatch_qty": 250, "spec": "HGH20"},
    "ORD-EXACT":    {"cust_name": "通用客户B", "dispatch_qty": 240, "spec": "HGW15"},
    "ORD-SINGLE":   {"cust_name": "通用客户C", "dispatch_qty": 50,  "spec": "HGH20"},
    "ORD-SPEC2":    {"cust_name": "通用客户D", "dispatch_qty": 192, "spec": "EGH15"},
}


def _norm(s: str) -> str:
    return (s or "").strip().replace("-", "").upper()


# 规范号映射: 去-大写 → (规范工单号, 记录). 命中后回带 - 的规范号给前端显示.
_NORM_ORDERS = {_norm(k): (k, v) for k, v in ORDERS.items()}


def lookup(job_no: str) -> List[Dict[str, Any]]:
    """按工单号查; 未知工单返回空数组 (模拟 MES 查不到)."""
    hit = _NORM_ORDERS.get(_norm(job_no))
    if hit is None:
        return []
    canon, rec = hit
    return [{
        "job_no": canon,                    # 回带连字符的规范号 (前端显示用)
        "cust_name": rec.get("cust_name", ""),
        "dispatch_qty": rec["dispatch_qty"],
        "spec": rec["spec"],
    }]


@app.api_route("/mes/work-order", methods=["GET", "POST"])
async def work_order(request: Request):
    """支持 GET ?job_no= 与 POST body{job_no} 两种发法 (适配不同 pull_cfg 模板)."""
    job_no = (request.query_params.get("job_no")
              or request.query_params.get("jobNo")
              or request.query_params.get("order_no"))
    if not job_no:
        try:
            body = await request.json()
            if isinstance(body, dict):
                job_no = body.get("job_no") or body.get("jobNo") or body.get("order_no")
        except Exception:
            job_no = None
    return {"code": 0, "msg": "ok", "data": lookup(job_no or "")}


def _extract_job_no(request: "Request", body: Any) -> str:
    job_no = (request.query_params.get("job_no")
              or request.query_params.get("jobNo")
              or request.query_params.get("order_no"))
    if not job_no and isinstance(body, dict):
        # 上银: {"api": "...", "parameters": {"job_no": "..."}}
        params = body.get("parameters") if isinstance(body.get("parameters"), dict) else {}
        job_no = (body.get("job_no") or body.get("jobNo") or body.get("order_no")
                  or params.get("job_no") or params.get("jobNo") or params.get("order_no"))
    return job_no or ""


@app.api_route("/hiwin/webcn/ai_error_prevention_job_info/query",
               methods=["GET", "POST"])
async def hiwin_query(request: Request):
    """上银真实返回格式 (与客户 D1 确认的契约一致).

    - 工单存在     → statusCode=200 + response.resultData=[{job_no,dispatch_qty,spec}]
    - 工单不存在   → statusCode=200 + response.resultData=[]  (客户: 据此判查无此单)
    - JOB-ERR/异常 → statusCode=500 + error{errorCode,errorInfo,detail_message} (HTTP 500)
    """
    try:
        body = await request.json()
    except Exception:
        body = None
    job_no = _extract_job_no(request, body)

    if _norm(job_no) == _norm("JOB-ERR"):
        err_payload = {
            "error": {
                "errorCode": 1,
                "errorInfo": "SYSTEM ERROR~~~~",
                "detail_message": "ERROR: column reference \"last_update_dt\" is ambiguous",
            },
            "statusCode": 500,
            "response": None,
            "success": False,
            "locale": "zh-CN",
        }
        return JSONResponse(status_code=500, content=err_payload)

    return {
        "error": None,
        "statusCode": 200,
        "response": {
            "pageNo": 1,
            "numberOfPerPage": 200,
            "resultData": lookup(job_no),
            "i18nInfo": None,
        },
        "success": True,
        "locale": "zh-CN",
    }


@app.get("/health")
async def health():
    return {"ok": True, "orders": sorted(ORDERS.keys())}


if __name__ == "__main__":  # pragma: no cover
    port = int(os.environ.get("MES_MOCK_PORT", "9100"))
    print(f"[MES Mock] 上银包装线 mock 启动 :{port}  工单: {sorted(ORDERS.keys())}")
    uvicorn.run(app, host="0.0.0.0", port=port)
