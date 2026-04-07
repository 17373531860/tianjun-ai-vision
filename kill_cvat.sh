#!/bin/bash
echo "[CVAT守护] 开始监控 (PID $$)"
while true
do
    count=$(pgrep -fc "migrateredis" 2>/dev/null || echo 0)
    if [ "$count" -gt "0" ]
    then
        pkill -9 -f "migrateredis" 2>/dev/null
        echo "[$(date +%H:%M:%S)] 杀掉 $count 个 migrateredis"
    fi
    sleep 3
done
