"""仿真上银 HIWIN MES 工单查询接口 (联调/仿真测试用).

完全复刻真实对接格式 (docs/customers/上银/上银MES工单拉取_对接问讯_致贵司IT.md 第五节):
  - POST /java_demo_test/api, Content-Type: application/json;charset=UTF-8
  - 请求体: {"api": "hiwin/webcn/ai_error_prevention_job_info/query",
             "parameters": {"job_no": "<工单号>"}}
  - 响应:   {"statusCode": 200, "success": true,
             "response": {"resultData": [ {job_no, cust_name, dispatch_qty, spec}, ... ]}}
  - 查不到 → statusCode=200 + resultData=[] (与真实 MES 行为一致, 曾在现场踩过)

用法: python tests/uat/mock_hiwin_mes_server.py  (默认端口 18080)
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 18080

# 仿真工单库 (按需增删)
ORDERS = {
    "111": {"job_no": "111", "cust_name": "测试专用公司",
            "dispatch_qty": 288, "spec": "SYS1"},
    "222": {"job_no": "222", "cust_name": "测试专用公司",
            "dispatch_qty": 96, "spec": "SYS1"},
    "333": {"job_no": "333", "cust_name": "虹川精密机电(仿真)",
            "dispatch_qty": 192, "spec": "SYS1"},
    # --- v3.45 SY5 箱标签扫码授权 UAT 工单库 (2026-07-27) ---
    # 合格整单: 384 滑块 = 4 满箱 x96, spec 按名匹配 SY5 项目
    "JOB260700101": {"job_no": "JOB260700101", "cust_name": "上银科技(仿真SY5)",
                     "dispatch_qty": 384, "spec": "SY5"},
    # 尾箱单: 360 = 3x96 + 72 尾箱
    "JOB260700102": {"job_no": "JOB260700102", "cust_name": "上银科技(仿真SY5)",
                     "dispatch_qty": 360, "spec": "SY5"},
    # 不合格单: 排产量 0 (MES 数据异常)
    "JOB260700103": {"job_no": "JOB260700103", "cust_name": "上银科技(仿真SY5)",
                     "dispatch_qty": 0, "spec": "SY5"},
    # 单箱小单: 96 = 1 满箱 (快速场景用)
    "JOB260700104": {"job_no": "JOB260700104", "cust_name": "上银科技(仿真SY5)",
                     "dispatch_qty": 96, "spec": "SY5"},
    # 两箱单: 192 = 2x96 (对账/重扫场景用)
    "JOB260700105": {"job_no": "JOB260700105", "cust_name": "上银科技(仿真SY5)",
                     "dispatch_qty": 192, "spec": "SY5"},
    # JOB260700404: 故意不存在 → 查无此单 (resultData=[])
    # JOB260700500: 服务器 5xx (见 do_POST 特判)
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        api = body.get("api") or ""
        job_no = str(((body.get("parameters") or {}).get("job_no")) or "").strip()
        # 特判: JOB260700500 模拟 MES 服务器故障 (5xx)
        if job_no == "JOB260700500":
            data = b'{"statusCode": 500, "success": false, "message": "internal error"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json;charset=UTF-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            print(f"[MockMES] query job_no={job_no!r} → 模拟500", flush=True)
            return
        rows = []
        if api == "hiwin/webcn/ai_error_prevention_job_info/query" and job_no in ORDERS:
            rows = [ORDERS[job_no]]
        resp = {"statusCode": 200, "success": True,
                "response": {"resultData": rows}}
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        print(f"[MockMES] query job_no={job_no!r} → {len(rows)} 条", flush=True)

    def log_message(self, fmt, *args):
        pass  # 静默默认访问日志, 只留上面的业务行


if __name__ == "__main__":
    print(f"[MockMES] 仿真上银MES已启动 http://localhost:{PORT}/java_demo_test/api "
          f"(工单库: {list(ORDERS)})", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
