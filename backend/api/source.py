"""
输入源管理 API
支持 USB 摄像头、本地视频文件、本地图片
集成 YOLO 模型推理
集成会话和周期记录
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import cv2
import os
import shutil
import uuid
import threading
import time
import numpy as np
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from backend.core.config import settings
from backend.db.database import SessionLocal
from backend.models.models import DetectionSession, DetectionCycle, StepRecord, VideoClip, DataExportSetting

router = APIRouter()

# 全局变量管理视频源状态
class VideoSourceManager:
    def __init__(self):
        self.source_type = None  # 'camera', 'video', 'image'
        self.capture = None
        self.is_running = False
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self.capture_lock = threading.Lock()  # 保护 capture 对象的并发访问
        self.camera_index = 0
        self.video_path = None
        self.image_path = None
        self.width = 1280
        self.height = 720
        self.fps = 30
        self._thread = None
        
        # 视频播放控制
        self.video_speed = 1.0  # 视频倍速
        self.video_ended = False  # 视频是否已结束
        self.video_total_frames = 0  # 视频总帧数
        self.video_current_frame = 0  # 当前帧位置
        
        # YOLO 模型
        self.model = None
        self.model_path = None
        self.is_detecting = False
        self.current_detections = []
        self.detection_lock = threading.Lock()
        self.conf_threshold = 0.25
        self.iou_threshold = 0.45
        
        # 统计
        self.fps_actual = 0
        self.latency = 0
        self._fps_counter = 0
        self._fps_time = time.time()
        
        # 步骤截图 {step_name: base64_image}
        self.step_screenshots = {}
        self.step_counts = {}  # {step_name: count}
        self.step_last_seen = {}  # {step_name: timestamp} 最后一次检测到的时间
        self.step_start_time = {}  # {step_name: timestamp} 步骤开始检测的时间
        self.step_time_config = {}  # {step_name: {min_duration, max_duration, max_interval}}
        self.disappear_threshold = 1.0  # 默认消失阈值秒
        
        # 项目配置
        self.project_config = None
        self.step_conf_thresholds = {}  # {step_name: threshold}
        self.step_min_frames = {}  # {step_name: min_frames} 每个步骤的最少帧数配置
        self.step_consecutive_frames = {}  # {step_name: count} 跟踪每个标签连续出现的帧数
        self.step_frame_confirmed = {}  # {step_name: bool} 标记标签是否已确认（达到最少帧数）
        
        # 静态步骤配置
        self.step_detection_type = {}  # {step_name: 'dynamic'|'static'} 检测类型
        self.step_static_config = {}  # {step_name: {trigger_frames, join_cycle, trigger_event}}
        self.step_static_triggered = {}  # {step_name: bool} 静态步骤是否已触发（防止重复触发）
        
        # 事件与计数器
        self.counters = {}  # {counter_name: value}
        self.events_log = []  # 事件日志
        self.current_cycle_steps = []  # 当前周期检测到的步骤顺序
        self.cycle_complete = False
        
        # Cycle Time 统计
        self.cycle_start_time = None  # 当前周期开始时间
        self.cycle_times = []  # 记录最近的周期时间（最多保留100个）
        self.step_detection_times = {}  # 步骤检测时间 {step_name: timestamp}
        self.step_durations = {}  # 步骤耗时 {step_name: duration_seconds}
        self.step_intervals = {}  # 步骤间隔时间 {step_name: interval_from_previous}
        self.last_step_completed_time = None  # 上一个步骤完成的时间
        
        # ========== 会话和周期记录 ==========
        self.current_session_id = None  # 当前会话ID
        self.current_session_uuid = None  # 当前会话UUID
        self.current_cycle_id = None  # 当前周期ID
        self.current_cycle_uuid = None  # 当前周期UUID
        self.current_cycle_number = 0  # 当前周期序号
        self.cycle_step_records = []  # 当前周期的步骤记录 (用于批量保存)
        self.step_order_counter = 0  # 步骤顺序计数器
        self.recording_enabled = False  # 是否启用录制
        self.export_settings = None  # 导出设置缓存
        self.last_cycle_end_time = None  # 上一周期结束时间（用于计算周期间隔）
        
        # 视频录制
        self.video_writer = None  # 视频录制器
        self.cycle_video_writer = None  # 周期视频录制器
        self.step_video_writers = {}  # 步骤视频录制器 {step_label: writer}
        
    def _get_db_session(self):
        """获取数据库会话"""
        return SessionLocal()
    
    def _load_export_settings(self):
        """加载导出设置"""
        try:
            db = self._get_db_session()
            setting = db.query(DataExportSetting).first()
            if not setting:
                setting = DataExportSetting()
                db.add(setting)
                db.commit()
                db.refresh(setting)
            self.export_settings = {
                'record_step_duration': setting.record_step_duration,
                'record_step_interval': setting.record_step_interval,
                'record_cycle_duration': setting.record_cycle_duration,
                'record_counters': setting.record_counters,
                'record_step_video': setting.record_step_video,
                'record_cycle_video': setting.record_cycle_video,
                'record_session_video': setting.record_session_video,
                'video_quality': setting.video_quality,
                'video_fps': setting.video_fps
            }
            db.close()
        except Exception as e:
            print(f"加载导出设置失败: {e}")
            self.export_settings = {
                'record_step_duration': True,
                'record_step_interval': True,
                'record_cycle_duration': True,
                'record_counters': True,
                'record_step_video': False,
                'record_cycle_video': False,
                'record_session_video': False,
                'video_quality': 'medium',
                'video_fps': 30
            }
    
    def start_session(self, project_id: int) -> dict:
        """开始新的检测会话"""
        try:
            db = self._get_db_session()
            session_uuid = str(uuid.uuid4())[:8]
            session = DetectionSession(
                session_uuid=session_uuid,
                project_id=project_id,
                start_time=datetime.now(),
                status="running"
            )
            db.add(session)
            db.commit()
            db.refresh(session)
            
            self.current_session_id = session.id
            self.current_session_uuid = session_uuid
            self.current_cycle_number = 0
            self.recording_enabled = True
            
            # 加载导出设置
            self._load_export_settings()
            
            db.close()
            print(f"检测会话已创建: {session_uuid}")
            
            # 开始会话视频录制
            self.start_session_recording()
            
            return {"session_id": session.id, "session_uuid": session_uuid}
        except Exception as e:
            print(f"创建会话失败: {e}")
            return None
    
    def end_session(self):
        """结束当前检测会话"""
        if not self.current_session_id:
            print("end_session: 没有活动的会话")
            return
        
        session_id = self.current_session_id
        session_uuid = self.current_session_uuid
        print(f"end_session: 正在结束会话 {session_uuid} (ID: {session_id})")
        
        try:
            db = self._get_db_session()
            session = db.query(DetectionSession).filter(
                DetectionSession.id == session_id
            ).first()
            
            if session:
                session.end_time = datetime.now()
                session.status = "completed"
                
                # 计算统计
                cycles = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == session_id
                ).all()
                
                print(f"end_session: 找到 {len(cycles)} 个周期")
                
                if cycles:
                    session.total_cycles = len(cycles)
                    session.good_cycles = len([c for c in cycles if c.is_good])
                    session.ng_cycles = session.total_cycles - session.good_cycles
                    
                    durations = [c.duration for c in cycles if c.duration]
                    if durations:
                        session.avg_cycle_time = sum(durations) / len(durations)
                        session.min_cycle_time = min(durations)
                        session.max_cycle_time = max(durations)
                
                # 保存计数器快照
                session.counters_snapshot = self.counters.copy() if self.counters else {}
                
                print(f"end_session: 保存数据 - 周期数: {session.total_cycles}, 合格: {session.good_cycles}, 不良: {session.ng_cycles}, 计数器: {session.counters_snapshot}")
                
                db.commit()
                print(f"会话已结束: {session_uuid}, 周期数: {session.total_cycles}")
            else:
                print(f"end_session: 未找到会话 ID={session_id}")
            
            db.close()
        except Exception as e:
            print(f"结束会话失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 停止视频录制
            self.stop_session_recording()
            self.stop_cycle_recording()
            
            self.current_session_id = None
            self.current_session_uuid = None
            self.recording_enabled = False
    
    def start_cycle(self):
        """开始新的检测周期"""
        if not self.current_session_id or not self.recording_enabled:
            return
        
        try:
            db = self._get_db_session()
            now = datetime.now()
            self.current_cycle_number += 1
            cycle_uuid = str(uuid.uuid4())[:8]
            
            # 更新上一周期的间隔时间
            if self.last_cycle_end_time is not None:
                interval_from_last = (now - self.last_cycle_end_time).total_seconds()
                # 查找上一周期并更新
                last_cycle = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == self.current_session_id,
                    DetectionCycle.cycle_number == self.current_cycle_number - 1
                ).first()
                if last_cycle:
                    last_cycle.interval_to_next = round(interval_from_last, 2)
                    db.commit()
                    print(f"上一周期间隔: {interval_from_last:.2f}s")
            
            cycle = DetectionCycle(
                cycle_uuid=cycle_uuid,
                session_id=self.current_session_id,
                cycle_number=self.current_cycle_number,
                start_time=now
            )
            db.add(cycle)
            db.commit()
            db.refresh(cycle)
            
            self.current_cycle_id = cycle.id
            self.current_cycle_uuid = cycle_uuid
            self.cycle_step_records = []
            self.step_order_counter = 0
            
            db.close()
            print(f"新周期开始: #{self.current_cycle_number} ({cycle_uuid})")
            
            # 开始周期视频录制
            self.start_cycle_recording()
        except Exception as e:
            print(f"创建周期失败: {e}")
    
    def end_cycle(self, is_good: bool, event_id: int = None, event_name: str = None, reason: str = None):
        """结束当前检测周期"""
        if not self.current_cycle_id or not self.recording_enabled:
            return
        
        # 停止周期视频录制
        self.stop_cycle_recording()
        
        try:
            db = self._get_db_session()
            cycle = db.query(DetectionCycle).filter(
                DetectionCycle.id == self.current_cycle_id
            ).first()
            
            if cycle:
                cycle.end_time = datetime.now()
                cycle.duration = (cycle.end_time - cycle.start_time).total_seconds()
                cycle.is_good = is_good
                cycle.event_id = event_id
                cycle.event_name = event_name
                cycle.result_reason = reason
                cycle.step_sequence = self.current_cycle_steps.copy()
                
                # 记录周期结束时间，用于计算下一周期的间隔
                self.last_cycle_end_time = cycle.end_time
                
                db.commit()
                print(f"周期结束: #{self.current_cycle_number}, 结果: {'OK' if is_good else 'NG'}, 耗时: {cycle.duration:.2f}s")
            
            db.close()
        except Exception as e:
            print(f"结束周期失败: {e}")
        finally:
            self.current_cycle_id = None
            self.current_cycle_uuid = None
    
    def record_step(self, step_label: str, step_name: str, start_time: float, end_time: float, 
                    duration: float, interval: float = None, confidence: float = None, is_valid: bool = True,
                    video_info: dict = None):
        """记录步骤信息
        
        注意：interval 现在表示"到下一步骤的间隔"，在下一步骤开始时计算并更新
        video_info: 视频信息字典，包含 video_uuid 和 filepath
        """
        if not self.current_cycle_id or not self.recording_enabled:
            return
        
        if not self.export_settings or not self.export_settings.get('record_step_duration', True):
            return
        
        try:
            db = self._get_db_session()
            self.step_order_counter += 1
            
            # 更新上一个步骤的"到下一步间隔"
            if self.cycle_step_records:
                last_record = self.cycle_step_records[-1]
                last_end_time = last_record.get('end_time')
                if last_end_time:
                    interval_to_next = start_time - last_end_time
                    # 更新数据库中的上一条步骤记录
                    last_step = db.query(StepRecord).filter(
                        StepRecord.cycle_id == self.current_cycle_id,
                        StepRecord.step_order == len(self.cycle_step_records)
                    ).first()
                    if last_step:
                        last_step.interval_to_next = round(interval_to_next, 2)
                        db.commit()
            record_uuid = str(uuid.uuid4())[:8]
            
            # 获取步骤ID（从项目配置）
            step_id = None
            if self.project_config:
                for step in self.project_config.get('steps_config', []):
                    if step.get('label') == step_label:
                        step_id = step.get('id')
                        break
            
            # 视频信息
            video_id = None
            video_path = None
            if video_info:
                video_id = video_info.get('video_uuid')
                video_path = video_info.get('filepath')
            
            record = StepRecord(
                record_uuid=record_uuid,
                cycle_id=self.current_cycle_id,
                step_id=step_id,
                step_label=step_label,
                step_name=step_name or step_label,
                step_order=self.step_order_counter,
                start_time=datetime.fromtimestamp(start_time),
                end_time=datetime.fromtimestamp(end_time),
                duration=duration,
                interval_from_prev=interval,
                confidence=confidence,
                is_valid=is_valid,
                video_id=video_id,
                video_path=video_path
            )
            db.add(record)
            db.commit()
            
            # 保存到本地记录用于间隔计算
            self.cycle_step_records.append({
                'step_label': step_label,
                'end_time': end_time
            })
            
            db.close()
        except Exception as e:
            print(f"记录步骤失败: {e}")
            import traceback
            traceback.print_exc()
        
    def set_project_config(self, config: dict):
        """设置项目配置"""
        self.project_config = config
        
        # 解析步骤置信度阈值和时间配置
        self.step_conf_thresholds = {}
        self.step_time_config = {}
        self.step_min_frames = {}  # 最少帧数配置
        self.step_consecutive_frames = {}  # 重置连续帧计数
        self.step_frame_confirmed = {}  # 重置确认状态
        self.step_detection_type = {}  # 检测类型
        self.step_static_config = {}  # 静态步骤配置
        self.step_static_triggered = {}  # 静态步骤触发状态
        
        steps_config = config.get('steps_config', [])
        for step in steps_config:
            if step.get('enabled', True):
                label = step.get('label', '')
                # 前端发送的是百分比（10-100），需要转换为小数（0.1-1.0）
                threshold = step.get('threshold', 50)
                if threshold > 1:
                    threshold = threshold / 100.0  # 转换百分比为小数
                self.step_conf_thresholds[label] = threshold
                
                # 步骤时间配置
                self.step_time_config[label] = {
                    'min_duration': step.get('min_duration'),  # 最短持续时间
                    'max_duration': step.get('max_duration'),  # 最大持续时间
                    'max_interval': step.get('max_interval', 1.0)  # 去重间隔，默认1秒
                }
                
                # 最少帧数配置（默认1帧）
                min_frames = step.get('min_frames')
                self.step_min_frames[label] = min_frames if min_frames and min_frames > 0 else 1
                
                # 检测类型配置
                detection_type = step.get('detection_type', 'dynamic')
                self.step_detection_type[label] = detection_type
                
                # 静态步骤配置
                if detection_type == 'static':
                    self.step_static_config[label] = {
                        'trigger_frames': step.get('static_trigger_frames', 30),
                        'join_cycle': step.get('join_cycle', True),
                        'trigger_event': step.get('triggerEvent')  # 触发的事件
                    }
                    self.step_static_triggered[label] = False
        
        # 初始化计数器
        self.counters = {}
        counters_config = config.get('counters_config', [])
        for counter in counters_config:
            self.counters[counter.get('name', '')] = counter.get('value', 0)
        
        # 重置周期状态
        self.current_cycle_steps = []
        self.cycle_complete = False
        self.events_log = []
        self.step_start_time = {}  # 重置步骤开始时间
        
        print(f"项目配置已加载: {config.get('name', 'Unknown')}")
        print(f"步骤阈值: {self.step_conf_thresholds}")
        print(f"步骤时间配置: {self.step_time_config}")
        print(f"步骤最少帧数: {self.step_min_frames}")
        print(f"步骤检测类型: {self.step_detection_type}")
        print(f"静态步骤配置: {self.step_static_config}")
        print(f"计数器: {self.counters}")
    
    def load_model(self, model_path: str) -> bool:
        """加载 YOLO 模型"""
        try:
            from ultralytics import YOLO
            self.model = YOLO(model_path)
            self.model_path = model_path
            print(f"模型加载成功: {model_path}")
            if hasattr(self.model, 'names'):
                print(f"类别: {list(self.model.names.values())}")
            return True
        except Exception as e:
            print(f"模型加载失败: {e}")
            self.model = None
            return False
    
    def _capture_loop(self):
        """摄像头/视频捕获循环"""
        frame_start_time = time.time()
        
        while self.is_running and self.capture is not None:
            speed = getattr(self, 'video_speed', 1.0)
            
            # 对于视频输入源，如果倍速大于1，通过跳帧实现
            if self.source_type == 'video' and speed > 1:
                # 跳过一些帧来实现倍速
                frames_to_skip = int(speed) - 1
                for _ in range(frames_to_skip):
                    ret = self.capture.grab()  # 只抓取不解码，更快
                    if not ret:
                        break
            
            try:
                ret, frame = self.capture.read()
            except Exception as e:
                ret = False
            
            if ret:
                original_frame = frame.copy()
                
                # 更新视频当前帧位置
                if self.source_type == 'video' and self.capture is not None:
                    self.video_current_frame = int(self.capture.get(cv2.CAP_PROP_POS_FRAMES))
                
                # 如果正在检测，执行推理
                if self.is_detecting and self.model is not None:
                    try:
                        start_time = time.time()
                        # 只获取检测结果，不在帧上绘制（让前端绘制）
                        detections = self._detect_only(frame)
                        
                        # 更新步骤统计和截图
                        self._update_step_stats(detections, original_frame)
                        
                        # 计算延迟
                        self.latency = int((time.time() - start_time) * 1000)
                        
                        with self.detection_lock:
                            self.current_detections = detections
                    except Exception as e:
                        pass  # 静默处理检测异常
                
                # 发送原始帧（不带检测框）
                with self.frame_lock:
                    self.current_frame = original_frame
                
                # 写入视频录制器
                if self.is_detecting and self.recording_enabled:
                    write_start = time.time()
                    self.write_frame_to_recorders(original_frame)
                    write_time = int((time.time() - write_start) * 1000)
                    if write_time > 50:
                        print(f"[慢写入警告] 写入帧耗时={write_time}ms")
                
                # FPS 计算
                self._fps_counter += 1
                if time.time() - self._fps_time >= 1.0:
                    self.fps_actual = self._fps_counter
                    self._fps_counter = 0
                    self._fps_time = time.time()
            else:
                # 视频结束，停止播放（不循环）
                if self.source_type == 'video' and self.video_path:
                    print("[Video] 视频播放完毕，已停止")
                    self.video_ended = True
                    self.is_running = False
                    # 如果正在检测，自动停止检测
                    if self.is_detecting:
                        self.stop_detection()
                    break  # 退出循环
                else:
                    time.sleep(0.01)
            
            # 计算帧处理耗时，动态调整 sleep 时间
            frame_elapsed = time.time() - frame_start_time
            target_interval = 1.0 / max(self.fps * speed, 1)
            sleep_time = max(0, target_interval - frame_elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
            frame_start_time = time.time()
    
    def _get_first_sequence_step_label(self):
        """获取顺序模式下配置的第一个步骤标签"""
        if not self.project_config:
            return None
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        sequence_order = pipeline_config.get('sequence_order', [])
        steps_config = self.project_config.get('steps_config', [])
        
        if not sequence_order or not steps_config:
            return None
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
        
        # 获取第一个步骤的标签
        first_item = sequence_order[0]
        first_step_id = first_item.get('step_id')
        return id_to_label.get(first_step_id)
    
    def _get_last_sequence_step_label(self):
        """获取顺序模式下配置的最后一个步骤标签
        
        对于自定义模式，使用独立的 custom_sequence_order 配置
        """
        if not self.project_config:
            return None
        
        logic_mode = self.project_config.get('logic_mode', 'sequential')
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        # 根据模式选择不同的配置
        if logic_mode == 'custom':
            sequence_order = pipeline_config.get('custom_sequence_order', [])
        else:
            sequence_order = pipeline_config.get('sequence_order', [])
        
        if not sequence_order or not steps_config:
            return None
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
        
        # 获取最后一个步骤的标签
        last_item = sequence_order[-1]
        last_step_id = last_item.get('step_id')
        return id_to_label.get(last_step_id)
    
    def _is_condition_prefix(self, sequence_to_check: list) -> bool:
        """检查给定序列是否是任何自定义条件的前缀
        
        Args:
            sequence_to_check: 要检查的步骤标签序列
        
        Returns:
            如果是任何条件的前缀返回 True，否则返回 False
        """
        if not self.project_config:
            return False
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        custom_conditions = pipeline_config.get('custom_conditions', [])
        steps_config = self.project_config.get('steps_config', [])
        
        if not custom_conditions:
            return False
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
        
        # 检查每个条件
        for cond in custom_conditions:
            cond_sequence = cond.get('sequence', [])
            if not cond_sequence:
                continue
            
            # 将条件中的步骤ID转换为标签
            cond_labels = [id_to_label.get(sid) for sid in cond_sequence if sid in id_to_label]
            
            if not cond_labels:
                continue
            
            # 检查 sequence_to_check 是否是 cond_labels 的前缀
            if len(sequence_to_check) <= len(cond_labels):
                is_prefix = True
                for i, label in enumerate(sequence_to_check):
                    if label != cond_labels[i]:
                        is_prefix = False
                        break
                if is_prefix:
                    print(f"  前缀匹配成功: {sequence_to_check} 是条件 {cond_labels} 的前缀")
                    return True
        
        return False
    
    def _settle_custom_cycle(self):
        """结算自定义模式的当前周期（在第一步重新出现且不匹配任何条件前缀时调用）"""
        if not self.project_config:
            return
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        custom_based_on = pipeline_config.get('custom_based_on')
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
        
        # 获取启用的步骤标签
        enabled_step_labels = [s.get('label') for s in steps_config if s.get('enabled', True)]
        
        print(f"自定义模式结算: 当前序列={self.current_cycle_steps}")
        
        # 先检查自定义条件
        custom_conditions = pipeline_config.get('custom_conditions', [])
        if custom_conditions:
            sorted_conditions = sorted(custom_conditions, key=lambda c: c.get('priority', 999))
            
            for cond in sorted_conditions:
                cond_sequence = cond.get('sequence', [])
                cond_event_id = cond.get('event_id')
                
                if not cond_sequence or not cond_event_id:
                    continue
                
                cond_labels = [id_to_label.get(sid) for sid in cond_sequence if sid in id_to_label]
                
                if self.current_cycle_steps == cond_labels:
                    print(f"  → 条件匹配！触发事件 {cond_event_id}")
                    self._trigger_event(cond_event_id, f'自定义条件匹配: {cond_labels}')
                    self.current_cycle_steps = []
                    return
        
        # 没有条件匹配，回退到基础模式判定
        if custom_based_on == 'sequential':
            # 使用自定义模式独立的顺序配置
            sequence_order = pipeline_config.get('custom_sequence_order', [])
            
            if not sequence_order:
                self.current_cycle_steps = []
                return
            
            expected_labels = [id_to_label.get(item.get('step_id')) for item in sequence_order if item.get('step_id') in id_to_label]
            
            if not expected_labels:
                self.current_cycle_steps = []
                return
            
            # 检查序列长度是否超过预期（有重复步骤）
            if len(self.current_cycle_steps) > len(expected_labels):
                print(f"  → 序列长度超过预期，有重复步骤 → NG")
                self._trigger_event(2, f'序列包含重复步骤: {self.current_cycle_steps}')
            elif not all(label in self.current_cycle_steps for label in expected_labels):
                missing = [l for l in expected_labels if l not in self.current_cycle_steps]
                print(f"  → 周期不完整，缺少: {missing} → NG")
                self._trigger_event(2, f'周期不完整，缺少: {missing}')
            else:
                # 检查顺序
                cycle_order_correct = True
                last_idx = -1
                for label in expected_labels:
                    if label in self.current_cycle_steps:
                        idx = self.current_cycle_steps.index(label)
                        if idx < last_idx:
                            cycle_order_correct = False
                            break
                        last_idx = idx
                
                if cycle_order_correct:
                    print(f"  → 顺序正确 → OK")
                    self._trigger_event(1, '顺序正确完成')
                else:
                    print(f"  → 顺序错误 → NG")
                    self._trigger_event(2, '顺序错误')
        
        elif custom_based_on == 'detection':
            detection_step_ids = pipeline_config.get('custom_detection_steps', [])
            if detection_step_ids:
                detection_labels = [id_to_label.get(sid) for sid in detection_step_ids if sid in id_to_label]
            else:
                detection_labels = enabled_step_labels
            
            if detection_labels and all(label in self.current_cycle_steps for label in detection_labels):
                print(f"  → 检测完成 → OK")
                self._trigger_event(1, '检测完成')
        
        # 重置周期
        self.current_cycle_steps = []
    
    def _settle_sequential_cycle(self):
        """结算纯顺序模式的当前周期（在新周期开始前调用）
        
        注意：自定义模式不使用此方法，而是在"最后一步消失"时通过 _check_custom_sequential_mode 判定
        """
        if not self.project_config:
            return
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
        
        # 顺序模式的结算逻辑
        sequence_order = pipeline_config.get('sequence_order', [])
        
        if not sequence_order or not steps_config:
            self.current_cycle_steps = []
            return
        
        # 获取期望的步骤标签顺序
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            return
        
        print(f"顺序模式结算: 期望={expected_labels}, 实际={self.current_cycle_steps}")
        
        # 检查是否完整（包含所有预期步骤）
        if not all(label in self.current_cycle_steps for label in expected_labels):
            # 不完整，触发 NG
            missing = [l for l in expected_labels if l not in self.current_cycle_steps]
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
        else:
            # 完整，检查顺序
            cycle_order_correct = True
            last_idx = -1
            for label in expected_labels:
                idx = self.current_cycle_steps.index(label)
                if idx < last_idx:
                    cycle_order_correct = False
                    break
                last_idx = idx
            
            if cycle_order_correct:
                print(f"  → 顺序正确 → OK")
                self._trigger_event(1, '顺序正确完成')
            else:
                print(f"  → 顺序错误 → NG")
                self._trigger_event(2, '顺序错误')
        
        # 重置周期
        self.current_cycle_steps = []
    
    def _update_step_stats(self, detections: list, original_frame: np.ndarray):
        """更新步骤统计和截图
        
        注意：检测结果全部传给前端显示，但只有通过步骤置信度阈值的才计入统计
        """
        import base64
        current_time = time.time()
        detected_labels = set()  # 用于统计的标签（通过阈值的）
        frame_detected_labels = set()  # 本帧通过置信度阈值的标签（用于帧数过滤）
        
        for det in detections:
            label = det.get('label', '')
            confidence = det.get('confidence', 0)
            if not label:
                continue
            
            # 应用步骤特定的置信度阈值（来自项目配置）
            # 只有通过阈值的检测才计入统计，但所有检测都会返回给前端显示
            if self.step_conf_thresholds:
                threshold = self.step_conf_thresholds.get(label)
                if threshold is not None and confidence < threshold:
                    # 低于该步骤的阈值，不计入统计（但检测框仍会显示）
                    continue
            
            frame_detected_labels.add(label)
        
        # 帧数过滤：更新连续帧计数
        # 对于本帧检测到的标签，增加连续帧计数
        for label in frame_detected_labels:
            if label not in self.step_consecutive_frames:
                self.step_consecutive_frames[label] = 0
            self.step_consecutive_frames[label] += 1
            
            # 检查是否达到最少帧数要求
            min_frames = self.step_min_frames.get(label, 1)
            if self.step_consecutive_frames[label] >= min_frames:
                detected_labels.add(label)
                if not self.step_frame_confirmed.get(label):
                    self.step_frame_confirmed[label] = True
        
        # 对于本帧没有检测到的标签，重置连续帧计数
        all_configured_labels = set(self.step_conf_thresholds.keys()) if self.step_conf_thresholds else set()
        for label in all_configured_labels:
            if label not in frame_detected_labels:
                self.step_consecutive_frames[label] = 0
                self.step_frame_confirmed[label] = False
                # 重置静态步骤的触发状态（标签消失后可以再次触发）
                if label in self.step_static_triggered:
                    self.step_static_triggered[label] = False
        
        # 检查静态步骤是否达到触发条件
        for label in frame_detected_labels:
            if self.step_detection_type.get(label) == 'static':
                static_config = self.step_static_config.get(label, {})
                trigger_frames = static_config.get('trigger_frames', 30)
                trigger_event = static_config.get('trigger_event')
                
                # 检查是否达到静态触发帧数且未触发过
                if (self.step_consecutive_frames.get(label, 0) >= trigger_frames 
                    and not self.step_static_triggered.get(label, False)):
                    
                    self.step_static_triggered[label] = True
                    print(f"静态步骤 [{label}] 达到触发条件（{trigger_frames}帧），触发事件: {trigger_event}")
                    
                    # 触发事件
                    if trigger_event:
                        self._trigger_event(trigger_event, f'静态步骤触发: {label}')
        
        # 获取启用的步骤标签
        enabled_labels = set()
        if self.project_config:
            steps_config = self.project_config.get('steps_config', [])
            for step in steps_config:
                if step.get('enabled', True):
                    step_label = step.get('label', '')
                    if step_label:
                        enabled_labels.add(step_label)
        
        # 继续处理通过帧数过滤的标签
        for label in detected_labels:
            
            # 获取步骤时间配置
            time_config = self.step_time_config.get(label, {})
            max_interval = time_config.get('max_interval') or 1.0  # 去重间隔，默认1秒
            
            # 判断步骤是否"刚出现"（考虑去重间隔）
            # 如果上次检测到该步骤的时间距离现在超过 max_interval，则认为是"新的一次"
            if label in self.step_last_seen:
                time_since_last = current_time - self.step_last_seen[label]
                is_new_appearance = time_since_last > max_interval
            else:
                is_new_appearance = True
            
            # 获取逻辑模式
            logic_mode = self.project_config.get('logic_mode') if self.project_config else 'detection'
            pipeline_config = self.project_config.get('pipeline_config', {}) if self.project_config else {}
            custom_based_on = pipeline_config.get('custom_based_on')
            
            # 纯顺序模式下：检测到第一个步骤"重新出现"时，结算上一轮并开始新周期
            if logic_mode == 'sequential':
                first_step_label = self._get_first_sequence_step_label()
                if first_step_label and label == first_step_label:
                    if is_new_appearance and len(self.current_cycle_steps) > 0:
                        self._settle_sequential_cycle()
            
            # 自定义模式（基于顺序模式）：使用前缀匹配判断是否结算
            elif logic_mode == 'custom' and custom_based_on == 'sequential':
                first_step_label = self._get_first_sequence_step_label()
                if first_step_label and label == first_step_label:
                    if is_new_appearance and len(self.current_cycle_steps) > 0:
                        # 检查是否开启了"累积重复序列"选项
                        accumulate_repeats = pipeline_config.get('accumulate_repeats', False)
                        
                        # 检查 [当前序列] + [第一步] 是否是任何条件的前缀
                        potential_sequence = self.current_cycle_steps + [label]
                        print(f"自定义模式: 第一步 [{label}] 重新出现，检查前缀: {potential_sequence}")
                        
                        if self._is_condition_prefix(potential_sequence):
                            # 匹配条件前缀，继续累积，不结算
                            print(f"  → 匹配条件前缀，继续累积")
                        elif accumulate_repeats:
                            # 开启了累积重复序列，不提前结算，等最后一步完成后判定
                            print(f"  → 已开启累积重复序列，继续累积（等待最后一步判定）")
                        else:
                            # 不匹配任何条件前缀，结算当前序列
                            print(f"  → 不匹配任何条件前缀，结算当前序列")
                            self._settle_custom_cycle()
            
            # 如果是新出现，记录开始时间
            if is_new_appearance:
                self.step_start_time[label] = current_time
                self.step_detection_times[label] = current_time  # 记录步骤检测时间（用于工艺卡片显示）
                
                # 如果是周期的第一个步骤，记录周期开始时间并创建新周期
                if len(self.current_cycle_steps) == 0:
                    self.cycle_start_time = current_time
                    # 开始新的检测周期记录
                    self.start_cycle()
                
                # 开始步骤视频录制
                self.start_step_recording(label)
            
            self.step_last_seen[label] = current_time
            
            # 记录当前周期的步骤顺序（只记录启用的步骤）
            if is_new_appearance and label in enabled_labels:
                # 检查静态步骤是否参与周期
                should_join_cycle = True
                if self.step_detection_type.get(label) == 'static':
                    static_config = self.step_static_config.get(label, {})
                    should_join_cycle = static_config.get('join_cycle', True)
                
                if should_join_cycle:
                    # 自定义模式：记录完整序列（包括重复步骤）
                    # 其他模式：只记录首次出现
                    if logic_mode == 'custom':
                        self.current_cycle_steps.append(label)
                    elif label not in self.current_cycle_steps:
                        self.current_cycle_steps.append(label)
            
            # 保存/更新截图（每个步骤只保存最新的）
            x, y, w, h = det['x'], det['y'], det['w'], det['h']
            img_h, img_w = original_frame.shape[:2]
            
            # 计算裁剪区域（扩大一点范围）
            pad = 20
            cx1 = max(0, int(x * img_w) - pad)
            cy1 = max(0, int(y * img_h) - pad)
            cx2 = min(img_w, int((x + w) * img_w) + pad)
            cy2 = min(img_h, int((y + h) * img_h) + pad)
            
            if cx2 > cx1 and cy2 > cy1:
                crop = original_frame[cy1:cy2, cx1:cx2]
                _, buffer = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
                self.step_screenshots[label] = base64.b64encode(buffer).decode('utf-8')
        
        # 检查消失的步骤（完成计数）
        for label, last_time in list(self.step_last_seen.items()):
            if label not in detected_labels:
                # 获取步骤时间配置
                time_config = self.step_time_config.get(label, {})
                max_interval = time_config.get('max_interval') or 1.0  # 使用去重间隔作为消失阈值
                
                if current_time - last_time > max_interval:
                    # 计算持续时间
                    start_time = self.step_start_time.get(label, last_time)
                    duration = last_time - start_time
                    
                    # 检查持续时间是否在有效范围内
                    min_duration = time_config.get('min_duration')
                    max_duration = time_config.get('max_duration')
                    
                    is_valid = True
                    if min_duration is not None and duration < min_duration:
                        is_valid = False
                        print(f"步骤 {label} 持续时间 {duration:.2f}s 低于最短时间 {min_duration}s，忽略")
                    if max_duration is not None and duration > max_duration:
                        is_valid = False
                        print(f"步骤 {label} 持续时间 {duration:.2f}s 超过最大时间 {max_duration}s，忽略")
                    
                    # 清理状态
                    del self.step_last_seen[label]
                    if label in self.step_start_time:
                        del self.step_start_time[label]
                    
                    # 只有有效的检测才计数
                    if is_valid:
                        if label not in self.step_counts:
                            self.step_counts[label] = 0
                        self.step_counts[label] += 1
                        
                        # 记录步骤耗时
                        self.step_durations[label] = round(duration, 2)
                        
                        # 计算与上一步骤的间隔时间
                        if self.last_step_completed_time is not None:
                            interval = start_time - self.last_step_completed_time
                            self.step_intervals[label] = round(interval, 2)
                        else:
                            self.step_intervals[label] = 0
                        
                        # 更新上一个步骤完成时间为当前步骤的结束时间
                        self.last_step_completed_time = last_time
                        
                        print(f"步骤完成: {label}, 耗时 {duration:.2f}s, 间隔 {self.step_intervals.get(label, 0):.2f}s, 累计: {self.step_counts[label]}")
                        
                        # ========== 记录步骤到数据库 ==========
                        # 获取步骤显示名称
                        step_name = label
                        if self.project_config:
                            for step in self.project_config.get('steps_config', []):
                                if step.get('label') == label:
                                    step_name = step.get('display_name') or step.get('name') or label
                                    break
                        
                        # 停止步骤视频录制并获取视频信息
                        step_video_info = self.stop_step_recording(label)
                        
                        self.record_step(
                            step_label=label,
                            step_name=step_name,
                            start_time=start_time,
                            end_time=last_time,
                            duration=duration,
                            interval=self.step_intervals.get(label),
                            confidence=None,
                            is_valid=True,
                            video_info=step_video_info
                        )
                        
                        # 检查是否触发事件
                        self._check_events(label)
    
    def _check_events(self, completed_step: str):
        """检查是否触发事件"""
        if not self.project_config:
            return
        
        events_config = self.project_config.get('events_config', [])
        logic_mode = self.project_config.get('logic_mode', 'detection')
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        label_to_id = {}
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                label_to_id[label] = step_id
        
        # 获取启用的步骤标签
        enabled_step_labels = [s.get('label') for s in steps_config if s.get('enabled', True)]
        
        # 自定义模式
        if logic_mode == 'custom':
            custom_based_on = pipeline_config.get('custom_based_on')  # 'sequential', 'detection', 或 None
            custom_conditions = pipeline_config.get('custom_conditions', [])
            
            # 先检查自定义条件（按优先级排序）
            condition_matched = False
            if custom_conditions:
                # 按优先级排序（数字越小优先级越高）
                sorted_conditions = sorted(custom_conditions, key=lambda c: c.get('priority', 999))
                
                for cond in sorted_conditions:
                    cond_sequence = cond.get('sequence', [])
                    cond_event_id = cond.get('event_id')
                    
                    if not cond_sequence or not cond_event_id:
                        continue
                    
                    # 将条件中的步骤ID转换为标签
                    cond_labels = [id_to_label.get(sid) for sid in cond_sequence if sid in id_to_label]
                    
                    if not cond_labels:
                        continue
                    
                    print(f"自定义条件检查: 条件序列={cond_labels}, 当前周期={self.current_cycle_steps}")
                    
                    # 检查是否完全匹配（数量和顺序都要相同）
                    if self.current_cycle_steps == cond_labels:
                        print(f"  → 条件匹配！触发事件 {cond_event_id}")
                        self._trigger_event(cond_event_id, f'自定义条件匹配: {cond_labels}')
                        self.current_cycle_steps = []
                        return  # 匹配后不再检查其他条件和基础模式
            
            # 没有自定义条件匹配，回退到基础模式
            # 只在"最后一步"消失时才触发基础模式判定
            if custom_based_on == 'sequential':
                last_step_label = self._get_last_sequence_step_label()
                if last_step_label and completed_step == last_step_label:
                    # 最后一步消失，触发判定
                    self._check_custom_sequential_mode(pipeline_config, id_to_label)
            elif custom_based_on == 'detection':
                self._check_custom_detection_mode(pipeline_config, id_to_label, enabled_step_labels)
            # 如果 custom_based_on 为空，则只依赖自定义条件，不做额外处理
        
        # 顺序模式
        elif logic_mode == 'sequential':
            self._check_sequential_mode(pipeline_config, id_to_label)
        
        # 检测模式
        elif logic_mode == 'detection':
            self._check_detection_mode(pipeline_config, id_to_label, enabled_step_labels)
        
        # 检查步骤特定事件
        for step in steps_config:
            if step.get('label') == completed_step:
                trigger_event = step.get('trigger_event')
                if trigger_event:
                    self._trigger_event(trigger_event, f'步骤 {completed_step} 触发')
    
    def _check_sequential_mode(self, pipeline_config: dict, id_to_label: dict):
        """检查顺序模式"""
        sequence_order = pipeline_config.get('sequence_order', [])
        if not sequence_order:
            return
        
        # 将步骤ID转换为标签名
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            return
        
        print(f"顺序模式检查: 期望={expected_labels}, 当前周期={self.current_cycle_steps}")
        
        # 检查是否包含所有预期步骤
        if all(label in self.current_cycle_steps for label in expected_labels):
            # 检查顺序是否正确
            cycle_order_correct = True
            last_idx = -1
            for label in expected_labels:
                if label in self.current_cycle_steps:
                    idx = self.current_cycle_steps.index(label)
                    if idx < last_idx:
                        cycle_order_correct = False
                        break
                    last_idx = idx
            
            if cycle_order_correct:
                self._trigger_event(1, '顺序正确完成')  # 事件1: 合格
            else:
                self._trigger_event(2, '顺序错误')  # 事件2: 不良
            
            # 重置周期
            self.current_cycle_steps = []
    
    def _check_custom_sequential_mode(self, pipeline_config: dict, id_to_label: dict):
        """检查自定义模式（基于顺序模式）的判定
        
        与普通顺序模式的区别：
        1. 当前周期可能包含重复步骤
        2. 如果序列长度超过预期（有重复步骤）且不匹配任何自定义条件，判定为 NG
        3. 使用独立的 custom_sequence_order 配置
        """
        # 使用自定义模式独立的顺序配置
        sequence_order = pipeline_config.get('custom_sequence_order', [])
        if not sequence_order:
            self.current_cycle_steps = []
            return
        
        # 将步骤ID转换为标签名
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            return
        
        print(f"自定义模式（基于顺序）检查: 期望={expected_labels}, 当前周期={self.current_cycle_steps}")
        
        # 检查序列长度是否超过预期（有重复步骤）
        if len(self.current_cycle_steps) > len(expected_labels):
            # 序列包含重复步骤，且不匹配任何自定义条件（因为自定义条件检查已经在前面完成）
            print(f"  → 序列长度({len(self.current_cycle_steps)})超过预期({len(expected_labels)})，有重复步骤 → NG")
            self._trigger_event(2, f'序列包含重复步骤: {self.current_cycle_steps}')
            self.current_cycle_steps = []
            return
        
        # 检查是否包含所有预期步骤
        if not all(label in self.current_cycle_steps for label in expected_labels):
            # 不完整
            missing = [l for l in expected_labels if l not in self.current_cycle_steps]
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
            self.current_cycle_steps = []
            return
        
        # 检查顺序是否正确
        cycle_order_correct = True
        last_idx = -1
        for label in expected_labels:
            if label in self.current_cycle_steps:
                idx = self.current_cycle_steps.index(label)
                if idx < last_idx:
                    cycle_order_correct = False
                    break
                last_idx = idx
        
        if cycle_order_correct:
            print(f"  → 顺序正确 → OK")
            self._trigger_event(1, '顺序正确完成')
        else:
            print(f"  → 顺序错误 → NG")
            self._trigger_event(2, '顺序错误')
        
        self.current_cycle_steps = []
    
    def _check_detection_mode(self, pipeline_config: dict, id_to_label: dict, enabled_step_labels: list):
        """检查检测模式"""
        detection_step_ids = pipeline_config.get('detection_steps', [])
        
        # 将步骤ID转换为标签名
        if detection_step_ids:
            detection_labels = [id_to_label.get(sid) for sid in detection_step_ids if sid in id_to_label]
        else:
            detection_labels = enabled_step_labels
        
        if not detection_labels:
            return
        
        print(f"检测模式检查: 需要={detection_labels}, 当前周期={self.current_cycle_steps}")
        
        if all(label in self.current_cycle_steps for label in detection_labels):
            self._trigger_event(1, '检测完成')  # 事件1: 合格
            self.current_cycle_steps = []
    
    def _check_custom_detection_mode(self, pipeline_config: dict, id_to_label: dict, enabled_step_labels: list):
        """检查自定义模式（基于检测模式）
        
        使用独立的 custom_detection_steps 配置
        """
        detection_step_ids = pipeline_config.get('custom_detection_steps', [])
        
        # 将步骤ID转换为标签名
        if detection_step_ids:
            detection_labels = [id_to_label.get(sid) for sid in detection_step_ids if sid in id_to_label]
        else:
            detection_labels = enabled_step_labels
        
        if not detection_labels:
            return
        
        print(f"自定义模式（基于检测）检查: 需要={detection_labels}, 当前周期={self.current_cycle_steps}")
        
        if all(label in self.current_cycle_steps for label in detection_labels):
            self._trigger_event(1, '检测完成')  # 事件1: 合格
            self.current_cycle_steps = []
    
    def _trigger_event(self, event_id, reason: str):
        """触发事件"""
        if not self.project_config:
            return
        
        events_config = self.project_config.get('events_config', [])
        
        # 支持数字ID和字符串ID（如 1, 2 或 'event_1', 'event_2'）
        event = None
        for e in events_config:
            eid = e.get('id')
            # 匹配数字ID或字符串ID
            if eid == event_id or str(eid) == str(event_id):
                event = e
                break
            # 也支持 event_1 格式匹配 id=1
            if isinstance(event_id, str) and event_id.startswith('event_'):
                try:
                    num_id = int(event_id.split('_')[1])
                    if eid == num_id:
                        event = e
                        break
                except:
                    pass
        
        if not event:
            print(f"事件未找到: {event_id}")
            return
        
        print(f"触发事件: {event.get('name', event_id)} - {reason}")
        
        # 判断是否为合格事件（事件ID为1或者名称包含"合格"）
        current_event_id = event.get('id')
        is_good = current_event_id == 1
        
        # 如果是事件1（合格），计算并记录周期时间
        if current_event_id == 1 and self.cycle_start_time is not None:
            cycle_time = time.time() - self.cycle_start_time
            self.cycle_times.append(cycle_time)
            # 只保留最近100个周期时间
            if len(self.cycle_times) > 100:
                self.cycle_times = self.cycle_times[-100:]
            print(f"  周期时间: {cycle_time:.2f}s, 平均: {sum(self.cycle_times)/len(self.cycle_times):.2f}s")
        
        # 结束当前周期并记录到数据库
        self.end_cycle(
            is_good=is_good,
            event_id=current_event_id,
            event_name=event.get('name', ''),
            reason=reason
        )
        
        # 重置周期开始时间（无论是合格还是NG，都重置）
        self.cycle_start_time = None
        
        # 执行计数器动作（支持 delta 和 value 两种字段名）
        actions = event.get('actions', [])
        for action in actions:
            counter_name = action.get('counter_name', '')
            # 兼容前端的 delta 字段和后端的 value 字段
            value = action.get('delta', action.get('value', 1))
            if counter_name in self.counters:
                self.counters[counter_name] += value
                print(f"  计数器 {counter_name} += {value} => {self.counters[counter_name]}")
        
        # 记录事件
        self.events_log.append({
            'event_id': str(event.get('id', event_id)),
            'event_name': event.get('name', ''),
            'reason': reason,
            'timestamp': time.time(),
            'show_notification': event.get('show_notification', False),
            'toast_id': event.get('toast_id', 'ok' if event.get('id') == 1 else 'ng' if event.get('id') == 2 else 'ok')
        })
        
        # 触发报警器（如果已配置）
        try:
            from backend.api.alarm import alarm_manager
            event_type = f'event{current_event_id}'
            alarm_manager.trigger_alarm(event_type)
        except Exception as e:
            print(f"触发报警失败: {e}")
    
    def _detect_only(self, frame: np.ndarray) -> list:
        """只执行检测，返回检测结果（不绘制检测框）"""
        detections = []
        
        try:
            # 直接使用配置的置信度阈值，不做额外过滤
            results = self.model.predict(frame, conf=self.conf_threshold, iou=self.iou_threshold, imgsz=640, verbose=False)
            
            h, w = frame.shape[:2]
            
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    
                    # 获取类别名
                    if hasattr(self.model, 'names') and class_id in self.model.names:
                        class_name = self.model.names[class_id]
                    else:
                        class_name = f"class_{class_id}"
                    
                    # 记录检测结果（归一化坐标）
                    detections.append({
                        'x': float(x1 / w),
                        'y': float(y1 / h),
                        'w': float((x2 - x1) / w),
                        'h': float((y2 - y1) / h),
                        'confidence': confidence,
                        'class_id': class_id,
                        'label': class_name
                    })
        except Exception as e:
            print(f"检测错误: {e}")
            import traceback
            traceback.print_exc()
        
        return detections
    
    def _draw_box_simple(self, img, x1, y1, x2, y2, label, conf):
        """检测框绘制（使用 PIL 支持中文）"""
        color_bgr = (0, 255, 0)  # 绿色 BGR
        color_rgb = (0, 255, 0)  # 绿色 RGB
        thickness = 2
        
        # 绘制矩形框
        cv2.rectangle(img, (x1, y1), (x2, y2), color_bgr, thickness)
        
        # 使用 PIL 绘制中文标签
        try:
            img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(img_pil)
            font = self._get_chinese_font(18)
            
            # 标签文字
            text = f"{label} {int(conf * 100)}%"
            
            # 计算文字大小
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except:
                text_w, text_h = 100, 20
            
            # 标签背景位置
            bg_y1 = max(0, y1 - text_h - 8)
            bg_y2 = y1
            bg_x1 = x1
            bg_x2 = x1 + text_w + 10
            
            # 绘制标签背景
            draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=(0, 0, 0))
            
            # 绘制标签文字
            draw.text((x1 + 5, bg_y1 + 2), text, font=font, fill=color_rgb)
            
            # 转回 OpenCV 格式
            img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        except Exception as e:
            # 如果 PIL 失败，回退到 OpenCV（不支持中文）
            print(f"PIL 绘制失败: {e}")
            text = f"{label} {int(conf * 100)}%"
            cv2.putText(img, text, (x1 + 5, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_bgr, 2)
        
        return img
    
    def _get_chinese_font(self, size=20):
        """获取中文字体"""
        font_paths = [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "simhei.ttf"
        ]
        
        for path in font_paths:
            try:
                return ImageFont.truetype(path, size)
            except:
                continue
        
        return ImageFont.load_default()
    
    def _draw_box(self, img, x1, y1, x2, y2, label, conf):
        """绘制赛博朋克风格检测框（支持中文）"""
        color_bgr = (255, 238, 118)  # 冰蓝色 BGR
        color_rgb = (118, 238, 255)  # RGB for PIL
        line_len = min(int((x2-x1)*0.2), int((y2-y1)*0.2), 20)
        thickness = 2
        
        # 绘制四角
        cv2.line(img, (x1, y1), (x1 + line_len, y1), color_bgr, thickness)
        cv2.line(img, (x1, y1), (x1, y1 + line_len), color_bgr, thickness)
        cv2.line(img, (x2, y1), (x2 - line_len, y1), color_bgr, thickness)
        cv2.line(img, (x2, y1), (x2, y1 + line_len), color_bgr, thickness)
        cv2.line(img, (x1, y2), (x1 + line_len, y2), color_bgr, thickness)
        cv2.line(img, (x1, y2), (x1, y2 - line_len), color_bgr, thickness)
        cv2.line(img, (x2, y2), (x2 - line_len, y2), color_bgr, thickness)
        cv2.line(img, (x2, y2), (x2, y2 - line_len), color_bgr, thickness)
        
        # 使用 PIL 绘制中文标签
        img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        font = self._get_chinese_font(20)
        
        # 标签文字 (置信度百分比)
        conf_pct = int(conf * 100)
        text = f"{label} {conf_pct}%"
        
        # 计算文字大小
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except:
            text_w, text_h = 100, 20
        
        # 标签背景位置
        bg_y1 = max(0, y1 - text_h - 10)
        bg_y2 = y1
        bg_x1 = x1
        bg_x2 = x1 + text_w + 10
        
        # 绘制标签背景
        draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=(0, 0, 0), outline=color_rgb)
        
        # 绘制标签文字
        draw.text((x1 + 5, bg_y1 + 2), text, font=font, fill=color_rgb)
        
        # 转回 OpenCV 格式
        img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        
        # 中心点
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        cv2.line(img, (cx - 5, cy), (cx + 5, cy), color_bgr, 1)
        cv2.line(img, (cx, cy - 5), (cx, cy + 5), color_bgr, 1)
        
        return img
    
    def start_camera(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        """启动摄像头"""
        self.stop()
        
        # 等待一小段时间确保之前的资源已释放
        time.sleep(0.2)
        
        # 尝试打开摄像头（支持重试）
        max_retries = 3
        for attempt in range(max_retries):
            # Windows 上使用 DirectShow，Linux 上使用 V4L2
            import platform
            if platform.system() == "Windows":
                self.capture = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
            else:
                self.capture = cv2.VideoCapture(device_index)
            
            if self.capture.isOpened():
                break
            
            if attempt < max_retries - 1:
                print(f"[Camera] 打开摄像头失败，重试 {attempt + 2}/{max_retries}...")
                time.sleep(0.5)
        
        if not self.capture.isOpened():
            raise Exception(f"无法打开摄像头 {device_index}，请检查设备是否被其他程序占用")
        
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.capture.set(cv2.CAP_PROP_FPS, fps)
        
        self.source_type = 'camera'
        self.camera_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.is_running = True
        
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        
        return True
    
    def start_video(self, video_path: str, speed: float = None):
        """启动视频文件播放"""
        # 保存当前倍速设置（如果有的话）
        current_speed = self.video_speed if self.video_speed else 1.0
        
        self.stop()
        
        if not os.path.exists(video_path):
            raise Exception(f"视频文件不存在: {video_path}")
        
        self.capture = cv2.VideoCapture(video_path)
        if not self.capture.isOpened():
            raise Exception(f"无法打开视频文件: {video_path}")
        
        self.source_type = 'video'
        self.video_path = video_path
        self.fps = self.capture.get(cv2.CAP_PROP_FPS) or 30
        self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # 视频帧信息
        self.video_total_frames = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video_current_frame = 0
        self.video_ended = False
        # 使用传入的倍速，如果没有传入则保持之前的倍速
        self.video_speed = speed if speed is not None else current_speed
        print(f"视频总帧数: {self.video_total_frames}, 倍速: {self.video_speed}x")
        
        self.is_running = True
        
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        
        return True
    
    def set_video_speed(self, speed: float):
        """设置视频播放倍速"""
        if speed < 0.25 or speed > 16:
            raise ValueError("倍速必须在 0.25 到 16 之间")
        self.video_speed = speed
        print(f"[Video] 倍速已设置为: {speed}x")
    
    def set_video_progress(self, progress: float):
        """设置视频播放进度 (0-1)"""
        if self.source_type != 'video':
            raise Exception("当前不是视频输入源")
        
        if progress < 0 or progress > 1:
            raise ValueError("进度必须在 0 到 1 之间")
        
        # 使用锁保护 capture 对象的访问
        with self.capture_lock:
            # 先停止播放线程
            was_running = self.is_running
            if self.is_running:
                self.is_running = False
                # 等待线程结束（最多等待1秒）
                if self._thread and self._thread.is_alive():
                    self._thread.join(timeout=1.0)
            
            # 如果 capture 不存在或已关闭，重新打开视频
            if self.capture is None or not self.capture.isOpened():
                if self.video_path and os.path.exists(self.video_path):
                    self.capture = cv2.VideoCapture(self.video_path)
                    if not self.capture.isOpened():
                        raise Exception("无法重新打开视频文件")
                else:
                    raise Exception("视频文件不存在")
            
            target_frame = int(self.video_total_frames * progress)
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            self.video_current_frame = target_frame
            self.video_ended = False
        
        # 重新启动播放线程（在锁外启动，避免死锁）
        self.is_running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print(f"[Video] 进度已设置为: {progress*100:.1f}% (帧 {target_frame}/{self.video_total_frames})")
    
    def get_video_info(self):
        """获取视频播放信息"""
        if self.source_type != 'video':
            return None
        
        return {
            "total_frames": self.video_total_frames,
            "current_frame": self.video_current_frame,
            "progress": self.video_current_frame / max(self.video_total_frames, 1),
            "speed": self.video_speed,
            "ended": self.video_ended,
            "fps": self.fps,
            "duration": self.video_total_frames / max(self.fps, 1),
            "current_time": self.video_current_frame / max(self.fps, 1)
        }
    
    def set_image(self, image_path: str):
        """设置图片为输入源"""
        self.stop()
        
        if not os.path.exists(image_path):
            raise Exception(f"图片文件不存在: {image_path}")
        
        frame = cv2.imread(image_path)
        if frame is None:
            raise Exception(f"无法读取图片: {image_path}")
        
        self.source_type = 'image'
        self.image_path = image_path
        
        # 如果正在检测，对图片进行推理
        if self.is_detecting and self.model is not None:
            frame, detections = self._detect_and_draw(frame)
            with self.detection_lock:
                self.current_detections = detections
        
        with self.frame_lock:
            self.current_frame = frame
        self.is_running = True
        
        return True
    
    def start_detection(self, model_path: str = None):
        """开始检测"""
        if model_path and (self.model is None or self.model_path != model_path):
            if not self.load_model(model_path):
                raise Exception("模型加载失败")
        
        if self.model is None:
            raise Exception("未加载模型")
        
        self.is_detecting = True
        print("检测已启动")
        return True
    
    def stop_detection(self):
        """停止检测"""
        self.is_detecting = False
        with self.detection_lock:
            self.current_detections = []
        print("检测已停止")
    
    def reset_stats(self):
        """重置统计数据（计数器、步骤计数、截图等）"""
        # 重置步骤统计
        self.step_counts = {}
        self.step_screenshots = {}
        self.step_last_seen = {}
        self.step_start_time = {}  # 重置步骤开始时间
        self.step_detection_times = {}  # 重置步骤检测时间
        self.step_durations = {}  # 重置步骤耗时
        self.step_intervals = {}  # 重置步骤间隔时间
        self.last_step_completed_time = None  # 重置上一步骤完成时间
        
        # 重置计数器（保留计数器名称，值清零）
        for key in self.counters:
            self.counters[key] = 0
        
        # 重置周期状态
        self.current_cycle_steps = []
        self.cycle_complete = False
        self.events_log = []
        
        # 重置周期时间统计
        self.cycle_times = []
        self.cycle_start_time = None
        
        print("统计数据已重置")
    
    # ========== 视频录制功能 ==========
    
    def start_session_recording(self):
        """开始会话视频录制"""
        if not self.export_settings or not self.export_settings.get('record_session_video'):
            return
        
        try:
            filename = f"session_{self.current_session_uuid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            filepath = os.path.join(settings.SESSION_VIDEO_DIR, filename)
            
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            fps = self.export_settings.get('video_fps', 30)
            
            self.video_writer = cv2.VideoWriter(filepath, fourcc, fps, (self.width, self.height))
            print(f"开始录制会话视频: {filepath}")
            
            # 记录到数据库
            db = self._get_db_session()
            video_uuid = str(uuid.uuid4())[:8]
            video = VideoClip(
                video_uuid=video_uuid,
                clip_type='session',
                related_id=self.current_session_id,
                file_path=filepath,
                file_name=filename,
                start_time=datetime.now()
            )
            db.add(video)
            db.commit()
            
            # 更新会话的视频ID
            session = db.query(DetectionSession).filter(DetectionSession.id == self.current_session_id).first()
            if session:
                session.video_id = video_uuid
                session.video_path = filepath
                db.commit()
            
            db.close()
        except Exception as e:
            print(f"开始会话录制失败: {e}")
    
    def stop_session_recording(self):
        """停止会话视频录制"""
        if self.video_writer:
            try:
                self.video_writer.release()
                print("会话视频录制已停止")
                
                # 更新视频信息
                db = self._get_db_session()
                session = db.query(DetectionSession).filter(DetectionSession.id == self.current_session_id).first()
                if session and session.video_path:
                    video = db.query(VideoClip).filter(VideoClip.file_path == session.video_path).first()
                    if video:
                        video.end_time = datetime.now()
                        if os.path.exists(session.video_path):
                            video.file_size = os.path.getsize(session.video_path)
                        db.commit()
                db.close()
            except Exception as e:
                print(f"停止会话录制失败: {e}")
            finally:
                self.video_writer = None
    
    def start_cycle_recording(self):
        """开始周期视频录制"""
        if not self.export_settings or not self.export_settings.get('record_cycle_video'):
            return
        
        try:
            filename = f"cycle_{self.current_cycle_uuid}_{datetime.now().strftime('%H%M%S')}.mp4"
            filepath = os.path.join(settings.CYCLE_VIDEO_DIR, filename)
            
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            fps = self.export_settings.get('video_fps', 30)
            
            self.cycle_video_writer = cv2.VideoWriter(filepath, fourcc, fps, (self.width, self.height))
            print(f"开始录制周期视频: {filename}")
            
            # 记录到数据库
            db = self._get_db_session()
            video_uuid = str(uuid.uuid4())[:8]
            video = VideoClip(
                video_uuid=video_uuid,
                clip_type='cycle',
                related_id=self.current_cycle_id,
                file_path=filepath,
                file_name=filename,
                start_time=datetime.now()
            )
            db.add(video)
            db.commit()
            
            # 更新周期的视频ID
            cycle = db.query(DetectionCycle).filter(DetectionCycle.id == self.current_cycle_id).first()
            if cycle:
                cycle.video_id = video_uuid
                cycle.video_path = filepath
                db.commit()
            
            db.close()
        except Exception as e:
            print(f"开始周期录制失败: {e}")
    
    def stop_cycle_recording(self):
        """停止周期视频录制"""
        if self.cycle_video_writer:
            try:
                self.cycle_video_writer.release()
                print("周期视频录制已停止")
            except Exception as e:
                print(f"停止周期录制失败: {e}")
            finally:
                self.cycle_video_writer = None
    
    def start_step_recording(self, step_label: str):
        """开始步骤视频录制"""
        if not self.export_settings or not self.export_settings.get('record_step_video'):
            return None
        
        try:
            video_uuid = str(uuid.uuid4())[:8]
            filename = f"step_{step_label}_{video_uuid}_{datetime.now().strftime('%H%M%S')}.mp4"
            filepath = os.path.join(settings.STEP_VIDEO_DIR, filename)
            
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            fps = self.export_settings.get('video_fps', 30)
            
            writer = cv2.VideoWriter(filepath, fourcc, fps, (self.width, self.height))
            self.step_video_writers[step_label] = {
                'writer': writer,
                'filepath': filepath,
                'filename': filename,
                'video_uuid': video_uuid,
                'start_time': datetime.now()
            }
            print(f"开始录制步骤视频: {step_label} -> {filename}")
            return video_uuid
        except Exception as e:
            print(f"开始步骤录制失败: {e}")
            return None
    
    def stop_step_recording(self, step_label: str) -> dict:
        """停止步骤视频录制，返回视频信息"""
        if step_label not in self.step_video_writers:
            return None
        
        try:
            step_video = self.step_video_writers.pop(step_label)
            writer = step_video.get('writer')
            if writer:
                writer.release()
            
            # 保存视频信息到数据库
            db = self._get_db_session()
            video = VideoClip(
                video_uuid=step_video['video_uuid'],
                clip_type='step',
                related_id=self.current_cycle_id,
                file_path=step_video['filepath'],
                file_name=step_video['filename'],
                start_time=step_video['start_time'],
                end_time=datetime.now()
            )
            db.add(video)
            db.commit()
            db.close()
            
            print(f"步骤视频录制已停止: {step_label}")
            return {
                'video_uuid': step_video['video_uuid'],
                'filepath': step_video['filepath']
            }
        except Exception as e:
            print(f"停止步骤录制失败: {e}")
            return None
    
    def write_frame_to_recorders(self, frame):
        """将帧写入所有活动的录制器"""
        if frame is None:
            return
        
        try:
            # 写入会话视频
            if self.video_writer and self.video_writer.isOpened():
                self.video_writer.write(frame)
            
            # 写入周期视频
            if self.cycle_video_writer and self.cycle_video_writer.isOpened():
                self.cycle_video_writer.write(frame)
            
            # 写入所有活动的步骤视频
            for step_label, step_info in list(self.step_video_writers.items()):
                writer = step_info.get('writer')
                if writer and writer.isOpened():
                    writer.write(frame)
        except Exception as e:
            print(f"写入录制帧失败: {e}")
    
    def pause(self):
        """暂停：停止画面更新和检测，但保持当前帧"""
        self.is_running = False
        self.is_detecting = False
        # 等待线程退出
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        # 不清除 current_frame，保持画面停在当前帧
        # 不释放 capture，方便后续恢复
        with self.detection_lock:
            self.current_detections = []
        print("已暂停：画面和检测都停止")
    
    def resume(self):
        """恢复：从暂停状态恢复，重新启动视频流"""
        if self.capture is None:
            print("无法恢复：没有可用的视频源")
            return False
        
        self.is_running = True
        # 重新启动视频处理线程
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print("已恢复：视频流重新启动")
        return True
    
    def standby(self):
        """待机：只停止检测推理，画面继续播放"""
        self.is_detecting = False
        with self.detection_lock:
            self.current_detections = []
        print("已待机：检测停止，画面继续")
    
    def stop(self):
        """停止当前输入源（完全停止并释放资源）"""
        self.is_running = False
        self.is_detecting = False
        
        # 等待线程结束
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        
        # 释放摄像头/视频资源
        if self.capture:
            try:
                self.capture.release()
            except:
                pass
            self.capture = None
        
        # 等待一小段时间确保资源被系统释放
        time.sleep(0.1)
        
        self.source_type = None
        self.current_frame = None
        self._thread = None
        with self.detection_lock:
            self.current_detections = []
    
    def get_frame(self):
        """获取当前帧"""
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame.copy()
        return None
    
    def get_detections(self):
        """获取当前检测结果"""
        with self.detection_lock:
            return self.current_detections.copy()
    
    def generate_mjpeg(self):
        """生成 MJPEG 流"""
        while self.is_running:
            frame = self.get_frame()
            if frame is not None:
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ret:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            time.sleep(1.0 / max(self.fps, 1))

# 全局视频源管理器
video_manager = VideoSourceManager()


# Pydantic 模型
class CameraStartRequest(BaseModel):
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30

class VideoStartRequest(BaseModel):
    file_path: str
    speed: float = 1.0  # 视频倍速

class VideoSpeedRequest(BaseModel):
    speed: float  # 视频倍速

class VideoProgressRequest(BaseModel):
    progress: float  # 视频进度 (0-1)

class ImageSetRequest(BaseModel):
    file_path: str

class DetectionStartRequest(BaseModel):
    model_path: str
    conf: float = 0.25
    iou: float = 0.45


# 摄像头列表缓存
_cameras_cache = {
    "cameras": [],
    "last_update": 0,
    "cache_duration": 60  # 缓存60秒
}

def _get_camera_name_linux(index):
    """Linux: 尝试获取摄像头真实名称"""
    try:
        name_path = f"/sys/class/video4linux/video{index}/name"
        if os.path.exists(name_path):
            with open(name_path, 'r') as f:
                return f.read().strip()
    except:
        pass
    return None

def _detect_cameras_linux():
    """Linux: 快速检测摄像头（通过读取 /dev/video* 设备）"""
    import glob
    cameras = []
    video_devices = sorted(glob.glob("/dev/video*"))
    
    for device in video_devices:
        try:
            index = int(device.replace("/dev/video", ""))
            # 只检测偶数索引（Linux 上奇数通常是元数据设备）
            if index % 2 != 0:
                continue
            
            # 获取设备名称
            name = _get_camera_name_linux(index)
            if name:
                cameras.append({"index": index, "name": f"{name} (索引 {index})"})
            else:
                cameras.append({"index": index, "name": f"摄像头 {index}"})
        except:
            continue
    
    return cameras

def _detect_cameras_windows():
    """Windows: 检测摄像头（减少尝试次数）"""
    cameras = []
    # 获取当前正在使用的摄像头索引
    current_camera_index = None
    if video_manager.source_type == 'camera' and video_manager.is_running:
        current_camera_index = video_manager.camera_index
    
    # 只尝试前5个索引，减少等待时间
    for i in range(5):
        # 如果这个摄像头正在被使用，直接添加到列表（不尝试打开）
        if current_camera_index is not None and i == current_camera_index:
            cameras.append({
                "index": i,
                "name": f"摄像头 {i} (使用中)" if i > 0 else "默认摄像头 (索引 0, 使用中)"
            })
            continue
        
        try:
            # 设置较短的超时（Windows 上可能不生效，但尝试一下）
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)  # Windows 上用 DirectShow 更快
            if cap.isOpened():
                cameras.append({
                    "index": i,
                    "name": f"摄像头 {i}" if i > 0 else "默认摄像头 (索引 0)"
                })
                cap.release()
        except:
            continue
    
    return cameras

# API 端点
@router.get("/cameras")
def list_cameras(refresh: bool = False):
    """列出可用摄像头
    
    Args:
        refresh: 是否强制刷新缓存，默认使用缓存
    """
    import platform
    current_time = time.time()
    
    # 检查缓存是否有效
    if not refresh and _cameras_cache["cameras"] and \
       (current_time - _cameras_cache["last_update"]) < _cameras_cache["cache_duration"]:
        return {"cameras": _cameras_cache["cameras"], "cached": True}
    
    # 根据操作系统选择检测方法
    if platform.system() == "Linux":
        cameras = _detect_cameras_linux()
    else:
        cameras = _detect_cameras_windows()
    
    # 如果没有检测到任何摄像头，返回默认项
    if not cameras:
        cameras = [{"index": 0, "name": "默认摄像头 (索引 0)"}]
    
    # 更新缓存
    _cameras_cache["cameras"] = cameras
    _cameras_cache["last_update"] = current_time
    
    return {"cameras": cameras, "cached": False}

@router.post("/camera/start")
def start_camera(req: CameraStartRequest):
    """启动摄像头"""
    try:
        video_manager.start_camera(
            device_index=req.device_index,
            width=req.width,
            height=req.height,
            fps=req.fps
        )
        return {"status": "success", "message": "摄像头已启动"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/camera/stop")
def stop_camera():
    """停止摄像头"""
    video_manager.stop()
    return {"status": "success", "message": "摄像头已停止"}

@router.post("/video/upload")
async def upload_video(file: UploadFile = File(...)):
    """上传视频文件"""
    allowed_ext = {'.mp4', '.avi', '.mov', '.mkv'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="不支持的视频格式")
    
    unique_name = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(settings.VIDEO_UPLOAD_DIR, unique_name)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return {
        "status": "success",
        "file_path": file_path,
        "file_name": file.filename
    }

@router.post("/video/start")
def start_video(req: VideoStartRequest):
    """启动视频播放"""
    try:
        video_manager.start_video(req.file_path, req.speed)
        return {"status": "success", "message": "视频已开始播放", "speed": req.speed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/video/stop")
def stop_video():
    """停止视频"""
    video_manager.stop()
    return {"status": "success", "message": "视频已停止"}

@router.post("/video/speed")
def set_video_speed(req: VideoSpeedRequest):
    """设置视频播放倍速"""
    try:
        video_manager.set_video_speed(req.speed)
        return {"status": "success", "message": f"倍速已设置为 {req.speed}x", "speed": req.speed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/video/progress")
def set_video_progress(req: VideoProgressRequest):
    """设置视频播放进度"""
    try:
        video_manager.set_video_progress(req.progress)
        return {"status": "success", "message": f"进度已设置为 {req.progress*100:.1f}%", "progress": req.progress}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/video/info")
def get_video_info():
    """获取视频播放信息"""
    info = video_manager.get_video_info()
    if info is None:
        return {"status": "not_video", "message": "当前不是视频输入源"}
    return {"status": "success", **info}

@router.post("/image/upload")
async def upload_image(file: UploadFile = File(...)):
    """上传图片文件"""
    allowed_ext = {'.jpg', '.jpeg', '.png', '.bmp'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    
    unique_name = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(settings.IMAGE_UPLOAD_DIR, unique_name)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return {
        "status": "success",
        "file_path": file_path,
        "file_name": file.filename,
        "url": f"/uploads/images/{unique_name}"
    }

@router.post("/image/set")
def set_image(req: ImageSetRequest):
    """设置图片为输入源"""
    try:
        video_manager.set_image(req.file_path)
        return {"status": "success", "message": "图片已设置为输入源"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/detection/start")
def start_detection(req: DetectionStartRequest):
    """开始检测"""
    try:
        video_manager.conf_threshold = req.conf
        video_manager.iou_threshold = req.iou
        video_manager.start_detection(req.model_path)
        
        # 如果有项目配置，自动创建会话
        if video_manager.project_config and video_manager.project_config.get('id'):
            session_info = video_manager.start_session(video_manager.project_config['id'])
            if session_info:
                return {
                    "status": "success", 
                    "message": "检测已启动",
                    "session_id": session_info.get('session_id'),
                    "session_uuid": session_info.get('session_uuid')
                }
        
        return {"status": "success", "message": "检测已启动"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/detection/stop")
def stop_detection():
    """停止检测（只停止推理）并结束会话"""
    # 结束当前会话
    video_manager.end_session()
    video_manager.stop_detection()
    return {"status": "success", "message": "检测已停止"}

@router.post("/detection/pause")
def pause_detection():
    """暂停：停止画面更新和检测，画面停在当前帧"""
    video_manager.pause()
    return {"status": "success", "message": "已暂停"}

@router.post("/detection/resume")
def resume_detection():
    """恢复：从暂停状态恢复，重新启动视频流和检测"""
    if video_manager.resume():
        video_manager.is_detecting = True
        return {"status": "success", "message": "已恢复"}
    else:
        raise HTTPException(status_code=400, detail="无法恢复：没有可用的视频源")

@router.post("/detection/standby")
def standby_detection():
    """待机：只停止检测推理，画面继续播放"""
    video_manager.standby()
    return {"status": "success", "message": "已待机"}

@router.post("/detection/reset-stats")
def reset_detection_stats():
    """重置统计数据（计数器、步骤计数等）"""
    video_manager.reset_stats()
    return {"status": "success", "message": "统计数据已重置"}

@router.get("/detection/results")
def get_detection_results():
    """获取检测结果"""
    # 获取最新事件（用于显示提示框）
    recent_events = []
    if video_manager.events_log:
        # 只返回最近5秒内的事件
        current_time = time.time()
        recent_events = [
            e for e in video_manager.events_log 
            if current_time - e.get('timestamp', 0) < 5
        ]
    
    # 计算平均周期时间
    avg_cycle_time = 0
    if video_manager.cycle_times:
        avg_cycle_time = round(sum(video_manager.cycle_times) / len(video_manager.cycle_times), 2)
    
    return {
        "detections": video_manager.get_detections(),
        "fps": video_manager.fps_actual,
        "latency": video_manager.latency,
        "is_detecting": video_manager.is_detecting,
        "source_type": video_manager.source_type,
        "is_running": video_manager.is_running,
        "step_counts": video_manager.step_counts.copy(),
        "step_screenshots": video_manager.step_screenshots.copy(),
        "step_detection_times": video_manager.step_detection_times.copy(),
        "step_durations": video_manager.step_durations.copy(),
        "step_intervals": video_manager.step_intervals.copy(),
        "counters": video_manager.counters.copy(),
        "recent_events": recent_events,
        "average_cycle_time": avg_cycle_time
    }

class ProjectConfigRequest(BaseModel):
    project_id: int
    name: str
    logic_mode: str = 'detection'
    steps_config: list = []
    pipeline_config: dict = {}
    events_config: list = []
    counters_config: list = []

@router.post("/detection/set-project")
def set_project_config(req: ProjectConfigRequest):
    """设置项目配置"""
    video_manager.set_project_config({
        'id': req.project_id,
        'name': req.name,
        'logic_mode': req.logic_mode,
        'steps_config': req.steps_config,
        'pipeline_config': req.pipeline_config,
        'events_config': req.events_config,
        'counters_config': req.counters_config
    })
    return {"status": "success", "message": "项目配置已设置"}

@router.get("/status")
def get_source_status():
    """获取当前输入源状态"""
    return {
        "is_running": video_manager.is_running,
        "is_detecting": video_manager.is_detecting,
        "source_type": video_manager.source_type,
        "width": video_manager.width,
        "height": video_manager.height,
        "fps": video_manager.fps,
        "fps_actual": video_manager.fps_actual,
        "latency": video_manager.latency,
        "model_loaded": video_manager.model is not None
    }


def get_video_feed():
    """获取视频流（供 main.py 使用）"""
    return video_manager.generate_mjpeg()

def get_video_manager():
    """获取视频管理器实例"""
    return video_manager
