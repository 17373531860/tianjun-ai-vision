#!/bin/bash
echo "=========================================="
echo "  网络诊断脚本"
echo "  $(date)"
echo "=========================================="
echo ""

echo ">>> 1. 本机 WiFi IP 地址"
ip addr show wlp0s20f3 2>/dev/null | grep "inet " || ip addr show wlan0 2>/dev/null | grep "inet " || echo "未找到 WiFi 网卡"
echo ""

echo ">>> 2. 本机有线网卡 IP 地址"
ip addr show enp7s0 2>/dev/null | grep "inet \|state" || echo "未找到/未连接有线网卡"
echo ""

echo ">>> 3. Ping 主机 192.168.110.66"
ping -c 3 -W 2 192.168.110.66 2>&1
echo ""

echo ">>> 4. Ping 副机 192.168.110.70"
ping -c 3 -W 2 192.168.110.70 2>&1
echo ""

echo ">>> 5. 测试主机 8000 端口"
timeout 3 bash -c 'echo > /dev/tcp/192.168.110.66/8000' 2>&1 && echo "端口 8000 可达" || echo "端口 8000 不可达"
echo ""

echo ">>> 6. 测试副机 8000 端口"
timeout 3 bash -c 'echo > /dev/tcp/192.168.110.70/8000' 2>&1 && echo "端口 8000 可达" || echo "端口 8000 不可达"
echo ""

echo ">>> 7. ARP 表 (同网段已知设备)"
arp -n 2>/dev/null | grep "192.168.110" || ip neigh show 2>/dev/null | grep "192.168.110" || echo "无记录"
echo ""

echo ">>> 8. 路由表 (192.168.110 网段走哪个接口)"
ip route show | grep "192.168.110" || echo "无匹配路由"
ip route get 192.168.110.66 2>/dev/null
echo ""

echo ">>> 9. 是否有代理/VPN 干扰 (Mihomo)"
ip rule show 2>/dev/null | head -10
echo ""

echo ">>> 10. WiFi 信号和连接状态"
iwconfig wlp0s20f3 2>/dev/null | grep -E "ESSID|Signal|Bit Rate" || echo "无法获取 WiFi 信息"
echo ""

echo "=========================================="
echo "  诊断完成"
echo "=========================================="
