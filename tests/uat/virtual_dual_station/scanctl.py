"""假扫码器控制客户端: python scanctl.py SCAN <code> | STATE | CLEAR"""
import json
import socket
import sys

cmd = " ".join(sys.argv[1:]) or "STATE"
s = socket.create_connection(("127.0.0.1", 24002), timeout=5)
s.sendall(cmd.encode() + b"\n")
buf = b""
while not buf.endswith(b"\n"):
    d = s.recv(65536)
    if not d:
        break
    buf += d
s.close()
try:
    obj = json.loads(buf.decode())
    if "events" in obj:
        print(f"lamp={obj['lamp']} connected={obj['connected']}")
        for e in obj["events"][-25:]:
            print(f"  [{e['ts']}] {e['kind']} {e['detail']}")
    else:
        print(obj)
except Exception:
    print(buf.decode())
