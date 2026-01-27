from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List, Dict
import json
import asyncio

router = APIRouter()

class ConnectionManager:
    """WebSocket连接管理器"""
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {
            "results": [],  # 检测结果推送
            "status": [],   # 系统状态推送
            "logs": []      # 日志推送
        }
    
    async def connect(self, websocket: WebSocket, channel: str = "results"):
        await websocket.accept()
        if channel not in self.active_connections:
            self.active_connections[channel] = []
        self.active_connections[channel].append(websocket)
    
    def disconnect(self, websocket: WebSocket, channel: str = "results"):
        if channel in self.active_connections:
            if websocket in self.active_connections[channel]:
                self.active_connections[channel].remove(websocket)
    
    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)
    
    async def broadcast(self, message: dict, channel: str = "results"):
        """向指定频道的所有连接广播消息"""
        if channel in self.active_connections:
            disconnected = []
            for connection in self.active_connections[channel]:
                try:
                    await connection.send_json(message)
                except Exception:
                    disconnected.append(connection)
            # 清理断开的连接
            for conn in disconnected:
                self.active_connections[channel].remove(conn)

# 全局连接管理器实例
manager = ConnectionManager()

@router.websocket("/ws/results")
async def websocket_results(websocket: WebSocket):
    """检测结果实时推送"""
    await manager.connect(websocket, "results")
    try:
        # 导入检测服务
        from backend.services.detector import get_detection_service
        detection_service = get_detection_service()
        
        while True:
            # 获取检测结果并推送
            detections = detection_service.get_detections()
            status = detection_service.get_status()
            
            await websocket.send_json({
                "type": "detection",
                "detections": detections,
                "fps": status['fps'],
                "latency": status['latency'],
                "is_running": status['is_running']
            })
            
            # 控制推送频率
            await asyncio.sleep(0.033)  # ~30fps
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, "results")
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket, "results")

@router.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    """系统状态实时推送"""
    await manager.connect(websocket, "status")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "status")

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """日志实时推送"""
    await manager.connect(websocket, "logs")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "logs")

# 用于从其他模块调用的广播函数
async def broadcast_detection_result(result: dict):
    """广播检测结果"""
    await manager.broadcast(result, "results")

async def broadcast_system_status(status: dict):
    """广播系统状态"""
    await manager.broadcast(status, "status")

async def broadcast_log(log: dict):
    """广播日志"""
    await manager.broadcast(log, "logs")

def get_manager():
    """获取连接管理器"""
    return manager
