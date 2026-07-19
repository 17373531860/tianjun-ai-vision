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
