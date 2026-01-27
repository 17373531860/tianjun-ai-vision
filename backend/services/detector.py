"""
YOLO 模型推理服务
"""
import cv2
import numpy as np
import time
import threading
from typing import Optional, List, Dict, Any
from pathlib import Path

class YOLODetector:
    """YOLO 检测器"""
    
    def __init__(self):
        self.model = None
        self.model_path = None
        self.class_names = []
        self.is_loaded = False
        self.conf_threshold = 0.5
        self.iou_threshold = 0.45
        
    def load_model(self, model_path: str) -> bool:
        """加载 YOLO 模型"""
        try:
            from ultralytics import YOLO
            self.model = YOLO(model_path)
            self.model_path = model_path
            
            # 获取类别名称
            if hasattr(self.model, 'names'):
                self.class_names = list(self.model.names.values())
            
            self.is_loaded = True
            print(f"模型加载成功: {model_path}")
            print(f"类别: {self.class_names}")
            return True
        except Exception as e:
            print(f"模型加载失败: {e}")
            self.is_loaded = False
            return False
    
    def detect(self, frame: np.ndarray, conf: float = None) -> List[Dict[str, Any]]:
        """执行检测"""
        if not self.is_loaded or self.model is None:
            return []
        
        conf = conf or self.conf_threshold
        
        try:
            results = self.model(frame, conf=conf, iou=self.iou_threshold, verbose=False)
            detections = []
            
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                
                for box in boxes:
                    # 获取边界框坐标 (xyxy 格式)
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    class_name = self.class_names[class_id] if class_id < len(self.class_names) else f"class_{class_id}"
                    
                    # 转换为归一化坐标 (0-1)
                    h, w = frame.shape[:2]
                    detection = {
                        'x': float(x1 / w),
                        'y': float(y1 / h),
                        'w': float((x2 - x1) / w),
                        'h': float((y2 - y1) / h),
                        'x1': int(x1),
                        'y1': int(y1),
                        'x2': int(x2),
                        'y2': int(y2),
                        'confidence': confidence,
                        'class_id': class_id,
                        'label': class_name
                    }
                    detections.append(detection)
            
            return detections
        except Exception as e:
            print(f"检测失败: {e}")
            return []
    
    def draw_detections(self, frame: np.ndarray, detections: List[Dict], 
                       box_color=(0, 255, 0), ng_color=(0, 0, 255),
                       line_width=2, font_size=14, show_conf=True) -> np.ndarray:
        """在帧上绘制检测框"""
        frame_copy = frame.copy()
        
        for det in detections:
            x1, y1 = det['x1'], det['y1']
            x2, y2 = det['x2'], det['y2']
            label = det['label']
            conf = det['confidence']
            
            # 判断是否是 NG (可以根据实际逻辑判断)
            color = box_color if det.get('is_ok', True) else ng_color
            
            # 绘制边界框
            cv2.rectangle(frame_copy, (x1, y1), (x2, y2), color, line_width)
            
            # 准备标签文字
            text = label
            if show_conf:
                text = f"{label} {conf:.2f}"
            
            # 计算文字大小
            font_scale = font_size / 20.0
            (text_w, text_h), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 2)
            
            # 绘制标签背景
            cv2.rectangle(frame_copy, (x1, y1 - text_h - 10), (x1 + text_w + 5, y1), color, -1)
            
            # 绘制标签文字
            cv2.putText(frame_copy, text, (x1 + 2, y1 - 5), 
                       cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 2)
        
        return frame_copy


class DetectionService:
    """检测服务 - 管理视频流和模型推理"""
    
    def __init__(self):
        self.detector = YOLODetector()
        self.is_running = False
        self.current_detections = []
        self.detection_lock = threading.Lock()
        self._thread = None
        self.video_manager = None
        self.project_config = None
        self.fps = 0
        self.latency = 0
        self.frame_count = 0
        self.last_fps_time = time.time()
        
        # 事件回调
        self.on_detection_callback = None
        self.on_event_callback = None
        
        # 步骤追踪
        self.detected_steps = []
        self.step_history = []
        
    def set_video_manager(self, video_manager):
        """设置视频管理器"""
        self.video_manager = video_manager
    
    def load_model(self, model_path: str) -> bool:
        """加载模型"""
        return self.detector.load_model(model_path)
    
    def set_project_config(self, config: dict):
        """设置项目配置"""
        self.project_config = config
        self.detected_steps = []
        self.step_history = []
    
    def start(self):
        """启动检测"""
        if self.is_running:
            return
        
        self.is_running = True
        self._thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """停止检测"""
        self.is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
    
    def _detection_loop(self):
        """检测循环"""
        while self.is_running:
            if self.video_manager is None or not self.video_manager.is_running:
                time.sleep(0.1)
                continue
            
            start_time = time.time()
            
            # 获取当前帧
            frame = self.video_manager.get_frame()
            if frame is None:
                time.sleep(0.01)
                continue
            
            # 执行检测
            detections = self.detector.detect(frame)
            
            # 更新检测结果
            with self.detection_lock:
                self.current_detections = detections
            
            # 处理检测逻辑
            if detections and self.project_config:
                self._process_detections(detections)
            
            # 计算 FPS 和延迟
            end_time = time.time()
            self.latency = int((end_time - start_time) * 1000)
            self.frame_count += 1
            
            if end_time - self.last_fps_time >= 1.0:
                self.fps = self.frame_count
                self.frame_count = 0
                self.last_fps_time = end_time
            
            # 控制检测频率
            sleep_time = max(0.01, 1.0/30 - (end_time - start_time))
            time.sleep(sleep_time)
    
    def _process_detections(self, detections: List[Dict]):
        """处理检测结果，根据项目逻辑判断事件"""
        if not self.project_config:
            return
        
        steps_config = self.project_config.get('steps_config', [])
        logic_mode = self.project_config.get('logic_mode', 'sequential')
        events_config = self.project_config.get('events_config', [])
        
        # 获取当前帧检测到的步骤
        current_steps = []
        for det in detections:
            label = det['label']
            # 查找对应的步骤配置
            for step in steps_config:
                if step.get('label') == label and step.get('enabled', True):
                    conf_threshold = step.get('threshold', 0.5)
                    if det['confidence'] >= conf_threshold:
                        current_steps.append({
                            'id': step.get('id'),
                            'label': label,
                            'displayLabel': step.get('displayLabel', label),
                            'confidence': det['confidence'],
                            'detection': det
                        })
        
        if current_steps:
            # 记录步骤历史
            for step in current_steps:
                if not self.step_history or self.step_history[-1]['id'] != step['id']:
                    self.step_history.append(step)
            
            # 触发检测回调
            if self.on_detection_callback:
                self.on_detection_callback(current_steps, self.step_history)
    
    def get_detections(self) -> List[Dict]:
        """获取当前检测结果"""
        with self.detection_lock:
            return self.current_detections.copy()
    
    def get_status(self) -> Dict:
        """获取状态"""
        return {
            'is_running': self.is_running,
            'model_loaded': self.detector.is_loaded,
            'fps': self.fps,
            'latency': self.latency,
            'detection_count': len(self.current_detections)
        }
    
    def reset(self):
        """重置状态"""
        self.detected_steps = []
        self.step_history = []
        with self.detection_lock:
            self.current_detections = []


# 全局检测服务实例
detection_service = DetectionService()

def get_detection_service() -> DetectionService:
    return detection_service
