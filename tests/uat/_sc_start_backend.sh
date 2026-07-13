#!/bin/bash
# sensor-clean 实测用: 主环境后端启动 (tianjun conda env, 8001)
cd "/home/qianqian/桌面/word/tianjun-main"
exec /home/qianqian/anaconda3/envs/tianjun/bin/python -m uvicorn backend.main:app \
  --host 0.0.0.0 --port 8001 --log-level warning
