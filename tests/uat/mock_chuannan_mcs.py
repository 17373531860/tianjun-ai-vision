#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""川南火工 中控系统(MCS) 模拟器 —— 对接联调用「对方的生产系统」。

严格按《生产管控软件对接·接口落实回复 v3》的字段与错误码搭建, 用来跟天军 AI 视觉
检测系统做真实的双向联调:

  方向 A→B (我方=川南中控 主动推给 天军):
    - 开工:  POST {天军入站URL}  body={TaskNo,ProductCode,StepCode,Operator,BeginTime}
            期望天军响应 {code,message}; code=0 成功 / 40001 重复 / 40004 缺字段 ...

  方向 B→A (天军 主动推给 我方=川南中控, 本模拟器作为服务端接收):
    - 报警:  POST /api/v1/warning/report
    - 完工:  POST /api/v1/task/complete
    - 健康:  GET  /health  -> {code:0,message:"ok"}

启动:
    python tests/uat/mock_chuannan_mcs.py            # 默认 9100 端口
    MCS_PORT=9100 TIANJUN_INBOUND=http://127.0.0.1:8001/api/v1/mes/inbound/task \
        python tests/uat/mock_chuannan_mcs.py

打开 http://127.0.0.1:9100 即可手点「发开工/发完工」并实时看双向报文。
本文件不带 test_ 前缀, 不会被 pytest 收集; 是联调/UAT 工具。
"""
import os
import json
import time
import asyncio
import datetime
import threading

import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

MCS_PORT = int(os.environ.get("MCS_PORT", "9100"))
# 天军(B)入站接收地址 —— 联调时以天军最终锁定地址为准
TIANJUN_INBOUND = os.environ.get(
    "TIANJUN_INBOUND", "http://127.0.0.1:8001/api/v1/mes/inbound/task")

app = FastAPI(title="川南中控系统(模拟器)")

# ──────────────── 通讯流水(线程安全, 内存环形) ────────────────
_LOG_LOCK = threading.Lock()
_LOG: list[dict] = []


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _record(direction: str, kind: str, detail: dict):
    """direction: 'send'(A→B) / 'recv'(B→A); kind: 业务名。"""
    with _LOG_LOCK:
        _LOG.append({
            "seq": len(_LOG) + 1,
            "ts": _now(),
            "direction": direction,
            "kind": kind,
            "detail": detail,
        })
        if len(_LOG) > 500:
            del _LOG[:-500]


# ──────────────── A→B: 主动推送(开工) ────────────────
def _push_to_tianjun(payload: dict) -> dict:
    """把一条业务报文 POST 给天军入站, 返回 {http, code, message, raw}。"""
    try:
        r = requests.post(TIANJUN_INBOUND, json=payload, timeout=10)
        try:
            body = r.json()
        except Exception:
            body = {"_text": r.text[:500]}
        result = {
            "http": r.status_code,
            "code": body.get("code"),
            "message": body.get("message"),
            "raw": body,
        }
    except Exception as e:
        result = {"http": None, "code": None, "message": f"连接天军失败: {e}", "raw": {}}
    return result


@app.post("/drive/start")
async def drive_start(req: Request):
    """模拟「开工信息推送」给天军。body 可带 task_no/product_code/step_code/operator。"""
    body = await _safe_json(req)
    task_no = body.get("task_no") or f"TASK-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    payload = {
        "TaskNo": task_no,
        "ProductCode": body.get("product_code", "PROD-X9-02"),
        "StepCode": body.get("step_code", "1.1"),
        "Operator": body.get("operator", "张三"),
        "BeginTime": _now(),
    }
    # 阻塞外发放到线程池, 释放事件循环, 否则我方同步回推完工时本模拟器的
    # /api/v1/task/complete 进不来 → 重入死锁 → 双向 read timeout。
    result = await asyncio.to_thread(_push_to_tianjun, payload)
    _record("send", "开工(A→B)", {"payload": payload, "resp": result})
    return JSONResponse({"sent": payload, "resp": result})


@app.post("/drive/raw")
async def drive_raw(req: Request):
    """直接把任意 body 当开工报文推给天军(用于测缺字段 40004 等异常态)。"""
    body = await _safe_json(req)
    result = await asyncio.to_thread(_push_to_tianjun, body)
    _record("send", "原始报文(A→B)", {"payload": body, "resp": result})
    return JSONResponse({"sent": body, "resp": result})


# ──────────────── B→A: 我方作为服务端接收 ────────────────
@app.post("/api/v1/task/complete")
async def recv_complete(req: Request):
    body = await _safe_json(req)
    _record("recv", "完工(B→A)", {"payload": body})
    return JSONResponse({"code": 0, "message": "ok"})


@app.post("/api/v1/warning/report")
async def recv_warning(req: Request):
    body = await _safe_json(req)
    # 报警截图很长, 流水里只留长度, 不刷屏
    img = body.get("Image") or body.get("image")
    shown = dict(body)
    if img:
        shown["Image"] = f"<JPEG Base64, {len(str(img))} chars>"
    _record("recv", "报警(B→A)", {"payload": shown})
    return JSONResponse({"code": 0, "message": "ok"})


@app.get("/health")
async def health():
    return JSONResponse({"code": 0, "message": "ok"})


# ──────────────── 流水查询 / 控制 ────────────────
@app.get("/log")
async def get_log():
    with _LOG_LOCK:
        return JSONResponse({"items": list(_LOG), "inbound": TIANJUN_INBOUND})


@app.post("/log/clear")
async def clear_log():
    with _LOG_LOCK:
        _LOG.clear()
    return JSONResponse({"ok": True})


async def _safe_json(req: Request) -> dict:
    try:
        return await req.json()
    except Exception:
        try:
            form = await req.form()
            return dict(form)
        except Exception:
            return {}


# ──────────────── 控制台页面 ────────────────
PAGE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<title>川南火工 · 中控系统(模拟器)</title>
<style>
 body{font-family:system-ui,Microsoft YaHei,sans-serif;margin:0;background:#0f1420;color:#e6edf3}
 header{padding:14px 20px;background:#161b29;border-bottom:1px solid #2a3550}
 header h1{margin:0;font-size:18px} header .sub{color:#8aa0c0;font-size:12px;margin-top:4px}
 .wrap{display:flex;gap:16px;padding:16px}
 .col{flex:1;background:#141a28;border:1px solid #28324d;border-radius:10px;padding:14px}
 .col h2{margin:0 0 10px;font-size:14px;color:#9fd0ff}
 button{cursor:pointer;border:0;border-radius:8px;padding:9px 12px;margin:4px 6px 4px 0;font-size:13px;color:#fff}
 .b-start{background:#2563eb} .b-start2{background:#7c3aed} .b-dup{background:#d97706} .b-bad{background:#dc2626}
 .b-clear{background:#374151} input{background:#0d1320;border:1px solid #28324d;color:#e6edf3;border-radius:6px;padding:6px 8px;width:130px}
 .row{margin:8px 0}
 .log{height:62vh;overflow:auto;font-family:ui-monospace,Consolas,monospace;font-size:12px;line-height:1.5}
 .ev{border-bottom:1px dashed #232c44;padding:6px 0}
 .send{color:#7dd3fc} .recv{color:#86efac}
 .ok{color:#4ade80} .err{color:#f87171}
 pre{margin:4px 0 0;white-space:pre-wrap;word-break:break-all;color:#c8d3e6}
</style></head><body>
<header><h1>川南火工 · 中控系统 (MCS 模拟器)</h1>
<div class=sub>天军入站地址: <b id=inb></b> ｜ 本模拟器作为服务端接收 天军 推来的 完工/报警</div></header>
<div class=wrap>
 <div class=col style="flex:.85">
   <h2>① 主动推送给天军 (A→B)</h2>
   <div class=row>任务号(留空自动生成): <input id=task placeholder="自动"></div>
   <div class=row>操作人员: <input id=op value="张三"> 工序工步: <input id=step value="1.1"></div>
   <div class=row>
     <button class=b-start onclick="start('PROD-X9-02')">发开工 · 产品A (PROD-X9-02)</button>
     <button class=b-start2 onclick="start('PROD-Y3-01')">发开工 · 产品B (PROD-Y3-01)</button>
   </div>
   <div class=row>
     <button class=b-dup onclick="dup()">重复开工(测 40001)</button>
     <button class=b-bad onclick="bad()">缺字段(测 40004)</button>
   </div>
   <div class=row><button class=b-clear onclick="clr()">清空流水</button></div>
   <p style="color:#8aa0c0;font-size:12px">说明: 「发开工」会以川南协议字段 POST 给天军, 并显示天军返回的 code/message。
   再点一次「发开工·产品A」即模拟「同工位最新开工」, 天军应顶替旧任务并把<b>完工</b>回推到本模拟器(见右栏)。</p>
 </div>
 <div class=col>
   <h2>② 双向通讯流水</h2>
   <div class=log id=log></div>
 </div>
</div>
<script>
let lastTask="";
function gv(id){return document.getElementById(id).value.trim()}
async function start(pc){
  const t=gv('task'); lastTask=t||("TASK-"+Date.now());
  const r=await fetch('/drive/start',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({task_no:t||lastTask,product_code:pc,operator:gv('op'),step_code:gv('step')})});
  await r.json(); refresh();
}
async function dup(){
  // 用上一次的任务号再发一遍 = 重复开工
  const t=lastTask||gv('task'); if(!t){alert('先发一次开工');return;}
  const r=await fetch('/drive/start',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({task_no:t,product_code:'PROD-X9-02',operator:gv('op'),step_code:gv('step')})});
  await r.json(); refresh();
}
async function bad(){
  const r=await fetch('/drive/raw',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({ProductCode:'PROD-X9-02',Operator:'张三'})}); // 故意不带 TaskNo
  await r.json(); refresh();
}
async function clr(){await fetch('/log/clear',{method:'POST'});refresh();}
function fmtResp(rp){
  if(!rp) return '';
  const cls=(rp.code===0)?'ok':'err';
  return `<span class=${cls}>← 天军响应 http=${rp.http} code=${rp.code} ${rp.message||''}</span>`;
}
async function refresh(){
  const r=await fetch('/log'); const d=await r.json();
  document.getElementById('inb').textContent=d.inbound;
  const box=document.getElementById('log');
  box.innerHTML=d.items.slice().reverse().map(e=>{
    const dir=e.direction==='send'?'send':'recv';
    const arrow=e.direction==='send'?'▲ 推给天军':'▼ 天军推来';
    let extra='';
    if(e.detail.resp) extra='<br>'+fmtResp(e.detail.resp);
    const pl=e.detail.payload||e.detail;
    return `<div class=ev><span class=${dir}>#${e.seq} [${e.ts}] ${arrow} · ${e.kind}</span>${extra}
      <pre>${JSON.stringify(pl,null,1)}</pre></div>`;
  }).join('');
}
setInterval(refresh,1200); refresh();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE


if __name__ == "__main__":
    print(f"[川南MCS模拟器] 启动于 http://127.0.0.1:{MCS_PORT}")
    print(f"[川南MCS模拟器] 天军入站地址 = {TIANJUN_INBOUND}")
    uvicorn.run(app, host="127.0.0.1", port=MCS_PORT, log_level="warning")
