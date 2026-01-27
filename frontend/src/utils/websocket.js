// src/utils/websocket.js

/**
 * 视觉系统 WebSocket 客户端
 * 支持自动重连、心跳检测
 */
export class VisionSocket {
  constructor(url) {
    // 自动构建完整 URL
    if (url.startsWith('/')) {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      this.url = `${protocol}//${host}${url}`;
    } else {
      this.url = url;
    }
    
    this.socket = null;
    this.onMessageCallback = null;
    this.onOpenCallback = null;
    this.onCloseCallback = null;
    this.onErrorCallback = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 10;
    this.reconnectInterval = 5000;
    this.heartbeatInterval = null;
    this.isManualClose = false;
  }

  connect() {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      return;
    }

    this.isManualClose = false;
    this.socket = new WebSocket(this.url);

    this.socket.onopen = () => {
      console.log("WebSocket 已连接:", this.url);
      this.reconnectAttempts = 0;
      this.startHeartbeat();
      if (this.onOpenCallback) {
        this.onOpenCallback();
      }
    };

    this.socket.onmessage = (event) => {
      // 忽略心跳响应
      if (event.data === 'pong') return;
      
      if (this.onMessageCallback) {
        try {
          const data = JSON.parse(event.data);
          this.onMessageCallback(data);
        } catch (e) {
          // 非 JSON 数据
          this.onMessageCallback(event.data);
        }
      }
    };

    this.socket.onclose = (event) => {
      console.log("WebSocket 连接断开:", event.code, event.reason);
      this.stopHeartbeat();
      
      if (this.onCloseCallback) {
        this.onCloseCallback(event);
      }
      
      // 非手动关闭时自动重连
      if (!this.isManualClose && this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++;
        console.log(`${this.reconnectInterval / 1000}秒后尝试第${this.reconnectAttempts}次重连...`);
        setTimeout(() => this.connect(), this.reconnectInterval);
      }
    };

    this.socket.onerror = (err) => {
      console.error("WebSocket 错误:", err);
      if (this.onErrorCallback) {
        this.onErrorCallback(err);
      }
    };
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeatInterval = setInterval(() => {
      if (this.socket && this.socket.readyState === WebSocket.OPEN) {
        this.socket.send('ping');
      }
    }, 30000); // 每30秒发送心跳
  }

  stopHeartbeat() {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  onMessage(callback) {
    this.onMessageCallback = callback;
  }

  onOpen(callback) {
    this.onOpenCallback = callback;
  }

  onClose(callback) {
    this.onCloseCallback = callback;
  }

  onError(callback) {
    this.onErrorCallback = callback;
  }

  send(data) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      if (typeof data === 'object') {
        this.socket.send(JSON.stringify(data));
      } else {
        this.socket.send(data);
      }
    } else {
      console.warn("WebSocket 未连接，无法发送数据");
    }
  }

  close() {
    this.isManualClose = true;
    this.stopHeartbeat();
    if (this.socket) {
      this.socket.close();
    }
  }

  getState() {
    if (!this.socket) return 'DISCONNECTED';
    switch (this.socket.readyState) {
      case WebSocket.CONNECTING: return 'CONNECTING';
      case WebSocket.OPEN: return 'OPEN';
      case WebSocket.CLOSING: return 'CLOSING';
      case WebSocket.CLOSED: return 'CLOSED';
      default: return 'UNKNOWN';
    }
  }
}

// 预定义的 WebSocket 连接
export const createResultsSocket = () => new VisionSocket('/ws/results');
export const createStatusSocket = () => new VisionSocket('/ws/status');
export const createLogsSocket = () => new VisionSocket('/ws/logs');