#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enhanced_sop_system_final_v2.py

天军科技AI-视觉AI行为防错检测系统
核心逻辑：
1. 检测到目标开始识别步骤
2. 检测存在期间实时更新截图
3. 目标消失后才确认步骤完成
4. 跳过步骤可以补充完成
5. 第12步完成后才重置
6. 手势检测使用MediaPipe实时显示手部关键点
"""

import sys
import time
import datetime
import psutil  # 新增系统监控
import csv
import os
import json
from pathlib import Path
from threading import Lock
from collections import deque

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont # 用于中文绘制

# PySide6 imports
from PySide6.QtCore import (Qt, QThread, Signal, QTimer, QDateTime, QSize, QPropertyAnimation, QEasingCurve, QSettings)
from PySide6.QtGui import (QImage, QPixmap, QColor, QFont, QCursor, QPainter, QPen, QCloseEvent)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QFileDialog, QSlider, QComboBox, QMessageBox, QFrame, 
    QGroupBox, QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy, QProgressBar, QStyleFactory, QLineEdit, QScrollArea,
    QAbstractItemView, QDialog, QDoubleSpinBox, QScrollArea, QInputDialog
)
try:
    from PySide6.QtMultimedia import QMediaDevices
    HAS_MULTIMEDIA = True
except ImportError:
    HAS_MULTIMEDIA = False

# ----------------- 默认配置 -----------------
DEFAULT_MODEL = "best.pt" 
DEFAULT_VIDEO = "input.mp4" 

# --- SOP 步骤定义 ---
SOP_STEPS = [
    '测硬度',   # 0
    '扫码',     # 1
    '激光打码', # 2 (已屏蔽)
    '上工件',   # 3 (已屏蔽)
    '下工件',   # 4
    '工件堆积'  # 5 (新步骤)
]
# 屏蔽的步骤ID (不参与逻辑，不显示)
# 注意：工件堆积 (ID 5) 不在屏蔽列表中
IGNORED_STEPS_IDS = [2, 3]

# 注意：列表顺序必须与模型训练时的类别ID一致
# -------------------------------------------

# ----------------- 样式表 (Cyberpunk Style - Ice Blue & Yellow Green) -----------------
INDUSTRIAL_STYLE = """
/* 全局背景 - 深邃蓝黑 */
QWidget {
    background-color: #0a1118;
    color: #a0e6ff;
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 12px;
}

/* 顶部标题栏 */
QFrame#Header {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0a1118, stop:0.5 #142838, stop:1 #0a1118);
    border-bottom: 1px solid #adff2f;
    border-top: 1px solid #005566;
}
QLabel#Title {
    color: #a0e6ff;
    font-size: 20px;
    font-weight: 900;
    letter-spacing: 3px;
    text-shadow: 0 0 10px #a0e6ff;
}
QLabel#Clock {
    color: #adff2f; 
    font-family: "Consolas", monospace;
    font-size: 14px;
    background-color: #142838;
    padding: 5px 10px;
    border: 1px solid #adff2f;
    border-radius: 4px;
}

/* 模块容器 */
QGroupBox {
    border: 1px solid #005566;
    border-radius: 4px;
    margin-top: 20px;
    font-weight: bold;
    color: #ffffff;
    background-color: rgba(20, 40, 56, 0.5);
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    left: 10px;
    background-color: #a0e6ff;
    color: #000000;
    border-radius: 2px;
}

/* 按钮通用 */
QPushButton {
    border: none;
    border-radius: 2px;
}

/* 左侧大按钮 */
QPushButton.BigBtn {
    background-color: rgba(160, 230, 255, 0.1);
    border: 1px solid #005566;
    color: #a0e6ff;
    padding: 8px;
    font-size: 12px;
    text-align: left;
}
QPushButton.BigBtn:hover {
    background-color: rgba(160, 230, 255, 0.3);
    border: 1px solid #a0e6ff;
    color: #ffffff;
}

/* 核心操作按钮 */
QPushButton.ActionBtn {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #006677, stop:1 #004455);
    color: white;
    border: 1px solid #a0e6ff;
    border-radius: 4px;
    font-weight: bold;
    padding: 4px;
}
QPushButton.ActionBtn:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #007788, stop:1 #006677);
    border: 1px solid #ffffff;
}
QPushButton.ActionBtn:pressed {
    background-color: #003344;
    padding-top: 5px;
    padding-left: 5px;
}

/* 特定按钮样式 */
QPushButton#BtnStart {
    background-color: #CEE9BE;
    border: 1px solid #CEE9BE;
    font-size: 14px;
    border-radius: 6px;
    color: #10202d;
    font-weight: bold;
}
QPushButton#BtnStart:hover {
    background-color: #e6f5dc;
    box-shadow: 0 0 10px #CEE9BE;
}
QPushButton#BtnStart[state="paused"] {
    background-color: #C9DEF8;
    border: 1px solid #C9DEF8;
}
QPushButton#BtnStart[state="paused"]:hover {
    background-color: #e4f0fc;
}

QPushButton#BtnReset {
    background-color: #a0e6ff;
    border: 1px solid #a0e6ff;
    color: #10202d;
    font-weight: bold;
}
QPushButton#BtnReset:hover {
    background-color: #bdf0ff;
    color: #10202d;
}

QPushButton#BtnClear { 
    background-color: #a0e6ff;
    border: 1px solid #a0e6ff;
    color: #10202d;
    font-weight: bold;
}
QPushButton#BtnClear:hover { 
    background-color: #bdf0ff;
    border: 1px solid #ffffff;
}

QPushButton#BtnExport {
    background-color: #a0e6ff;
    border: 1px solid #a0e6ff;
    color: #10202d;
    font-weight: bold;
}
QPushButton#BtnExport:hover {
    background-color: #bdf0ff;
    border: 1px solid #ffffff;
}

/* SOP 步骤样式 */
QLabel.StepPending {
    background-color: #10202d;
    border: 1px solid #284050;
    color: #557788;
    border-radius: 2px;
    padding: 2px;
    font-size: 11px;
}
QLabel.StepDetecting {
    background-color: rgba(160, 230, 255, 0.2);
    border: 1px solid #a0e6ff;
    color: #a0e6ff;
    border-radius: 2px;
    padding: 2px;
    font-size: 11px;
    font-weight: bold;
}
QLabel.StepDone {
    background-color: rgba(173, 255, 47, 0.2);
    border: 1px solid #adff2f;
    color: #adff2f;
    border-radius: 2px;
    padding: 2px;
    font-size: 11px;
    font-weight: bold;
}
QLabel.StepNG {
    background-color: rgba(255, 0, 0, 0.2);
    border: 1px solid #ff0000;
    color: #ff0000;
    border-radius: 2px;
    padding: 2px;
    font-size: 11px;
    font-weight: bold;
}

/* 视频区域边框 */
QLabel#VideoLabel {
    border: 2px solid #a0e6ff;
    background-color: #000;
}

/* 统计标签 */
QLabel.StatLabel {
    background-color: rgba(160, 230, 255, 0.05);
    border-left: 3px solid #adff2f;
    padding: 5px;
    font-family: "Consolas", monospace;
    font-size: 13px;
}

/* 进度条 */
QProgressBar {
    border: 1px solid #005566;
    background-color: #10202d;
    text-align: center;
    color: #fff;
    border-radius: 2px;
}
QProgressBar::chunk {
    background-color: #a0e6ff;
}

/* 输入框 */
QLineEdit {
    background-color: #10202d;
    border: 1px solid #005566;
    color: #a0e6ff;
    padding: 4px;
}

/* 表格 */
QTableWidget {
    background-color: #10202d;
    border: 1px solid #005566;
    gridline-color: #005566;
}
QHeaderView::section {
    background-color: #142838;
    color: #a0e6ff;
    border: 1px solid #005566;
    padding: 4px;
}

/* 滑动条 */
QSlider::groove:horizontal {
    border: 1px solid #005566;
    height: 4px;
    background: #10202d;
    margin: 2px 0;
}
QSlider::handle:horizontal {
    background: #adff2f;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}
"""

# ----------------- 自定义控件 -----------------
class VideoLabel(QLabel):
    mouse_moved = Signal(int, int)
    def __init__(self, text=""):
        super().__init__(text)
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("border: 2px solid #1c4b75; background-color: #000;")
        self.setScaledContents(False)
        self.setMinimumSize(160, 120)
        self._original_pixmap = None
        self._rotation_angle = 0  # 0, 90, 180, 270

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        self.mouse_moved.emit(pos.x(), pos.y())
        super().mouseMoveEvent(event)
    
    def setPixmap(self, pixmap):
        self._original_pixmap = pixmap
        self._updateScaledPixmap()
    
    def setRotation(self, angle):
        """设置旋转角度"""
        if self._rotation_angle != angle:
            self._rotation_angle = angle
            self._updateScaledPixmap()
    
    def _updateScaledPixmap(self):
        if not self._original_pixmap or self._original_pixmap.isNull():
            return
            
        # 根据旋转角度调整显示
        w = self.width()
        h = self.height()
        
        # 根据旋转角度调整宽高
        if self._rotation_angle == 90 or self._rotation_angle == 270:
            # 交换宽高
            scaled = self._original_pixmap.scaled(
                h, w,  # 注意这里交换了宽高
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
        else:
            scaled = self._original_pixmap.scaled(
                w, h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
        
        super().setPixmap(scaled)
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._updateScaledPixmap()

# ----------------- 推理线程 (检测消失后完成逻辑) -----------------
class InferenceWorker(QThread):
    frame_update = Signal(np.ndarray)
    magnifier_update = Signal(np.ndarray)
    step_screenshot = Signal(int, np.ndarray)  # 步骤号, 截图
    stats_update = Signal(float, float)
    # 更新信号：counts, detecting, ng, pt_times, all_completed
    sop_update = Signal(list, list, list, list, bool)
    statistics_update = Signal(dict)
    clear_screenshots = Signal()  # 新增：清空截图信号
    log_message = Signal(str, str, int) # 修改：增加帧索引参数
    alert_signal = Signal(str, str) # 新增：弹窗报警信号 (标题, 内容)
    finished_sig = Signal()
    
    def __init__(self, model_path, source, sop_steps, source_type='video'):
        super().__init__()
        self.model_path = model_path
        self.source = source
        self.sop_steps = sop_steps # 动态传入SOP步骤
        self.source_type = source_type
        
        self.running = True
        self.paused = False
        self.speed = 1.0 # 播放速度
        self.target_frame = -1 # 跳转目标帧
        self.conf = 0.25
        self.iou = 0.45
        self.step_confs = {}
        self.rotation_angle = 0  # 0, 90, 180, 270
        
        self._seek_seconds = 0
        self._seek_lock = Lock()
        
        # SOP 状态管理
        self.step_counts = [0] * len(self.sop_steps)  # 步骤累加计数
        self.step_detecting = [False] * len(self.sop_steps)  # 步骤是否正在检测中
        self.step_ng = [False] * len(self.sop_steps)  # 步骤是否NG
        self.step_pt_times = [""] * len(self.sop_steps) # 步骤最新PT时间
        self.all_completed = False
        
        # 记录每个步骤上次检测到的时间
        self.step_last_seen = {}
        self.step_start_times = {} # 记录步骤开始检测的时间用于计算PT
        self.disappear_threshold = 1.0  # 消失阈值：1秒未检测到即认为消失
        
        # 统计数据
        self.stats = {
            'total_rounds': 0,
            'qualified_rounds': 0, # 合格工件 (正常)
            'recheck_rounds': 0,   # 复检工件 (新)
            'ng_rounds': 0,        # NG/不合格工件 (原名目可能混用，这里明确为不合格轮次)
            'ok_count': 0, # OK步骤数
            'ng_count': 0, # NG步骤数
            'defect_rate': 0.0,
            'pt_count': 0,
            'avg_ct': 0.0 
        }
        
        self.round_start_time = time.time()
        self.ct_history = deque(maxlen=50)
        self.current_workpiece_ng = False # 当前工件是否NG
        
        # 新增逻辑状态
        self.cycle_scan_count = 0 # 当前轮次中“扫码”完成的次数 (用于判定正常/复检/NG)
        self.pile_up_detected = False # 工件堆积状态
        self.last_pile_up_time = 0 # 上次报警时间，避免重复频繁报警

        
        # FPS计算
        self.fps_deque = deque(maxlen=30)
        
        # 错误日志控制
        self.mp_init_error_logged = False
        
        # 漏做步骤检测
        self.round_step_flags = [False] * len(self.sop_steps) # 记录本轮已完成的步骤
        
        # --- NEW: 合格判定逻辑重构状态 ---
        self.unused_laser_count = 0  # 激光打码缓存池
        self.core_steps_mask = [False, False, False] # [0:测硬度, 1:扫码, 3:下工件]. ID 2 是激光打码
        # --------------------------------

    def set_speed(self, speed):
        self.speed = speed

    def set_pause(self, paused):
        self.paused = paused
        
    def seek(self, frame_index):
        self.target_frame = frame_index

    def run(self):
        try:
            # 加载检测模型
            from ultralytics import YOLO
            model = YOLO(self.model_path)
            self.log_message.emit("模型加载", f"检测模型: {Path(self.model_path).name}", -1)
            
        except Exception as e:
            self.log_message.emit("错误", f"模型加载失败: {e}", -1)
            self.finished_sig.emit()
            return
        
        cap = None
        image_frame = None
        
        # 打开输入源
        if self.source_type == 'image':
            image_frame = cv2.imread(self.source)
            if image_frame is None:
                self.log_message.emit("错误", "无法读取图片", -1)
                self.finished_sig.emit()
                return
        else:
            cap = cv2.VideoCapture(self.source)
            if not cap.isOpened():
                self.log_message.emit("错误", "无法打开视频源", -1)
                self.finished_sig.emit()
                return
            
            if self.source_type == 'camera':
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                self.log_message.emit("摄像头", "已启动", -1)
        
        prev_time = time.time()
        fps_val = 30
        if cap:
            fps_val = cap.get(cv2.CAP_PROP_FPS)
            if fps_val <= 0 or fps_val > 120:
                fps_val = 30
        
        frame_delay = 1.0 / fps_val
        
        while self.running:
            start_time = time.time()
            current_time = time.time()
            
            # 处理跳转
            just_seeked = False
            if self.target_frame >= 0 and cap:
                cap.set(cv2.CAP_PROP_POS_FRAMES, self.target_frame)
                self.target_frame = -1
            if self.paused and not just_seeked:
                time.sleep(0.05)
                continue
            
            # 1. 读取原始帧
            frame = None
            current_frame_idx = -1
            if self.source_type == 'image':
                frame = image_frame.copy()
            else:
                current_frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                ret, frame = cap.read()
                if not ret:
                    if self.source_type == 'video':
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    else:
                        self.log_message.emit("错误", "摄像头读取失败", -1)
                        break
            
            if frame is None:
                continue
            
            # 2. 保存原始帧用于检测
            original_frame = frame.copy()
            
            # 3. 在原始帧上进行推理（关键修改：使用低阈值获取所有候选框，后续手动过滤）
            t1 = time.time()
            # use minimal conf to catch everything, then filter by step_confs
            base_conf = 0.01 
            results = model.predict(original_frame, conf=base_conf, iou=self.iou, imgsz=640, verbose=False)
            t2 = time.time()
            infer_time = (t2 - t1) * 1000
            
            res = results[0]
            
            # 4. 绘制检测结果（自定义赛博朋克风格）
            annotated_frame = original_frame.copy()
            if res.boxes is not None:
                for box in res.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cls_id = int(box.cls[0])
                    if cls_id in IGNORED_STEPS_IDS: continue # 屏蔽不展示的步骤

                    conf = float(box.conf[0])
                    
                    # 按步骤过滤置信度
                    thresh = self.step_confs.get(cls_id, self.conf)
                    if conf < thresh:
                        continue
                    
                    # 使用中文名称 (确保SOP_STEPS定义了对应的中文)
                    if 0 <= cls_id < len(self.sop_steps):
                        label = self.sop_steps[cls_id]
                    else:
                        label = f"未知步骤 {cls_id}"
                        
                    annotated_frame = self.draw_cyberpunk_box(annotated_frame, x1, y1, x2, y2, label, conf)
            
            # ---------------- 手势关键点检测 (已移除) ----------------
            
            detected_classes = set()  # 本帧检测到的类别ID
            max_conf = -1
            best_crop = None
            
            if res.boxes is not None and len(res.boxes) > 0:
                for box in res.boxes:
                    cls_id = int(box.cls[0])
                    if cls_id in IGNORED_STEPS_IDS: continue # 屏蔽检测

                    conf = float(box.conf[0])
                    
                    # 按步骤过滤置信度
                    thresh = self.step_confs.get(cls_id, self.conf)
                    if conf < thresh:
                        continue
                    
                    detected_classes.add(cls_id)
                    self.stats['pt_count'] += 1
                    
                    # 找置信度最高的目标用于放大显示
                    if conf > max_conf:
                        max_conf = conf
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        h, w, _ = annotated_frame.shape
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(w, x2), min(h, y2)
                        
                        if x2 > x1 and y2 > y1:
                            best_crop = annotated_frame[y1:y2, x1:x2]
            
            # SOP步骤检测逻辑（检测消失后完成）
            self.check_sop_with_disappear(detected_classes, original_frame.copy(), current_time, current_frame_idx)
            
            # 发送放大图
            if best_crop is not None:
                self.magnifier_update.emit(cv2.cvtColor(best_crop, cv2.COLOR_BGR2RGB))
            
            # ---------------------------------------------
            
            # 5. 绘制HUD (新增)
            annotated_frame = self.draw_hud(annotated_frame)

            # 6. 旋转帧用于显示（关键修改：在检测后旋转）
            display_frame = self.rotate_frame(frame)
            annotated_frame = self.rotate_frame(annotated_frame)
            
            # FPS计算
            curr_time = time.time()
            fps_real = 1.0 / (curr_time - prev_time + 1e-6)
            prev_time = curr_time
            self.fps_deque.append(fps_real)
            avg_fps = np.mean(self.fps_deque)
            
            # 发送更新信号（使用旋转后的帧）
            self.frame_update.emit(cv2.cvtColor(annotated_frame, cv2.COLOR_BGR2RGB))
            self.stats_update.emit(infer_time, avg_fps)
            self.statistics_update.emit(self.stats.copy())
            
            if self.source_type == 'image':
                time.sleep(0.05)
            else:
                proc_time = time.time() - start_time
                wait = (frame_delay / self.speed) - proc_time # 应用播放速度
                if wait > 0:
                    time.sleep(wait)
        
        if cap:
            cap.release()
        self.finished_sig.emit()
    
    def draw_cyberpunk_box(self, img, x1, y1, x2, y2, label, conf):
        """绘制赛博朋克风格检测框 (支持中文)"""
        color = (255, 238, 118) # Ice Blue BGR
        line_len = min(int((x2-x1)*0.2), int((y2-y1)*0.2), 20)
        thickness = 2
        
        # 1. 绘制四角
        # Top-Left
        cv2.line(img, (x1, y1), (x1 + line_len, y1), color, thickness)
        cv2.line(img, (x1, y1), (x1, y1 + line_len), color, thickness)
        # Top-Right
        cv2.line(img, (x2, y1), (x2 - line_len, y1), color, thickness)
        cv2.line(img, (x2, y1), (x2, y1 + line_len), color, thickness)
        # Bottom-Left
        cv2.line(img, (x1, y2), (x1 + line_len, y2), color, thickness)
        cv2.line(img, (x1, y2), (x1, y2 - line_len), color, thickness)
        # Bottom-Right
        cv2.line(img, (x2, y2), (x2 - line_len, y2), color, thickness)
        cv2.line(img, (x2, y2), (x2, y2 - line_len), color, thickness)
        
        # 2. 标签背景与文字 (混合绘制：中文用PIL，数字用OpenCV)
        conf_text = f" {conf:.0%}"
        
        # 2.1 准备PIL绘制中文
        img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        
        # 尝试加载中文字体 - 增大字号
        font = None
        font_paths = [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "simhei.ttf"
        ]
        
        # 字号加大到 20
        font_size = 20
        for path in font_paths:
            try:
                font = ImageFont.truetype(path, font_size)
                break
            except:
                continue
        
        if font is None:
            font = ImageFont.load_default()
            
        # 计算中文宽度
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        w_zh = right - left
        
        # 2.2 计算数字宽度 (OpenCV) - 字号加大
        font_cv = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.7  # 加大
        thickness_num = 2 # 加粗
        (w_num, h_num), baseline = cv2.getTextSize(conf_text, font_cv, font_scale, thickness_num)
        
        total_w = w_zh + w_num
        
        # 2.3 绘制背景框 (OpenCV)
        # y1是框的顶部，标签在y1上方
        bg_h = 32 # 加高背景
        # 黑色实心背景，确保文字清晰
        cv2.rectangle(img, (x1, y1 - bg_h), (x1 + int(total_w) + 15, y1), (0, 0, 0), -1)
        # 冰山蓝边框，增加科技感
        cv2.rectangle(img, (x1, y1 - bg_h), (x1 + int(total_w) + 15, y1), color, 1)
        
        # 2.4 绘制中文 (PIL)
        # 注意：PIL绘制后需要转回OpenCV格式，再画数字，否则数字会被覆盖或者格式不对
        # 调整文字垂直位置
        text_y = y1 - bg_h + 4
        # 文字颜色改为冰山蓝 (RGB: 118, 238, 255) 以在黑色背景上高亮显示
        draw.text((x1 + 5, text_y), label, font=font, fill=(118, 238, 255))
        img[:] = cv2.cvtColor(np.asarray(img_pil), cv2.COLOR_RGB2BGR)[:]
        
        # 2.5 绘制数字 (OpenCV)
        # y坐标调整：OpenCV putText的y是基线
        # 文字颜色改为黄绿色 (BGR: 47, 255, 173)
        cv2.putText(img, conf_text, (x1 + 5 + int(w_zh), y1 - 8), font_cv, font_scale, (47, 255, 173), thickness_num, cv2.LINE_AA)

        
        # 3. 中心锁定点
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        cv2.line(img, (cx - 5, cy), (cx + 5, cy), color, 1)
        cv2.line(img, (cx, cy - 5), (cx, cy + 5), color, 1)
        
        return img

    def draw_hud(self, img):
        """绘制科技感HUD"""
        h, w = img.shape[:2]
        color = (255, 238, 118)  # Ice Blue BGR
        
        # 1. 四角边框
        l = 30  # 线长
        t = 2   # 线宽
        # 左上
        cv2.line(img, (10, 10), (10 + l, 10), color, t)
        cv2.line(img, (10, 10), (10, 10 + l), color, t)
        # 右上
        cv2.line(img, (w - 10, 10), (w - 10 - l, 10), color, t)
        cv2.line(img, (w - 10, 10), (w - 10, 10 + l), color, t)
        # 左下
        cv2.line(img, (10, h - 10), (10 + l, h - 10), color, t)
        cv2.line(img, (10, h - 10), (10, h - 10 - l), color, t)
        # 右下
        cv2.line(img, (w - 10, h - 10), (w - 10 - l, h - 10), color, t)
        cv2.line(img, (w - 10, h - 10), (w - 10, h - 10 - l), color, t)
        
        # 2. 中心十字准星
        cx, cy = w // 2, h // 2
        # 黄绿色准星 (BGR: 47, 255, 173)
        cv2.line(img, (cx - 20, cy), (cx + 20, cy), (47, 255, 173), 1)
        cv2.line(img, (cx, cy - 20), (cx, cy + 20), (47, 255, 173), 1)
        
        # 3. 顶部状态栏背景
        overlay = img.copy()
        cv2.rectangle(overlay, (0, 0), (w, 40), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.3, img, 0.7, 0, img)
        
        # 4. 文本信息
        font = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(img, "SYSTEM: ONLINE", (20, 25), font, 0.6, color, 1, cv2.LINE_AA)
        cv2.putText(img, "MODE: AI-SOP", (w - 150, 25), font, 0.6, color, 1, cv2.LINE_AA)
        
        # 5. 底部扫描线效果 (模拟)
        y_scan = int((time.time() * 200) % h)
        # 扫描线改为冰山蓝
        cv2.line(img, (0, y_scan), (w, y_scan), (255, 238, 118), 1)
        
        # 6. 右下角雷达 (新增)
        radar_radius = 50
        cx_r, cy_r = w - 70, h - 70
        # 雷达圈 - 深黄绿色
        radar_color = (20, 100, 60) 
        cv2.circle(img, (cx_r, cy_r), radar_radius, radar_color, 1)
        cv2.circle(img, (cx_r, cy_r), int(radar_radius*0.6), radar_color, 1)
        cv2.circle(img, (cx_r, cy_r), int(radar_radius*0.3), radar_color, 1)
        # 扫描扇形
        angle = int((time.time() * 180) % 360)
        x2 = int(cx_r + radar_radius * np.cos(np.radians(angle)))
        y2 = int(cy_r + radar_radius * np.sin(np.radians(angle)))
        # 扫描线 黄绿色
        cv2.line(img, (cx_r, cy_r), (x2, y2), (47, 255, 173), 2)
        # 随机目标点
        if int(time.time()) % 2 == 0:
            cv2.circle(img, (cx_r + 20, cy_r - 20), 3, (0, 0, 255), -1)

        return img

    def rotate_frame(self, frame):
        """根据当前旋转角度旋转帧"""
        if self.rotation_angle == 0:
            return frame
        elif self.rotation_angle == 90:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self.rotation_angle == 180:
            return cv2.rotate(frame, cv2.ROTATE_180)
        elif self.rotation_angle == 270:
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame
    
    def set_rotation(self, angle):
        """设置旋转角度"""
        self.rotation_angle = angle
        self.log_message.emit("视频旋转", f"设置旋转角度为: {angle}°", -1)
    
    def check_sop_with_disappear(self, detected_classes, frame_copy, current_time, current_frame_idx):
        """核心SOP逻辑：基于消失判断完成"""
        state_changed = False
        pile_up_id = 5
        if pile_up_id in detected_classes:
            # 如果之前没检测到堆积，或者是新的一次堆积（加时间间隔防止刷屏）
            if not self.pile_up_detected or (current_time - self.last_pile_up_time > 5.0):
                self.pile_up_detected = True
                self.last_pile_up_time = current_time
                self.current_workpiece_ng = True
                self.stats['ng_count'] += 1
                self.log_message.emit("🔥 严重异常", "工件堆积！", current_frame_idx)
                # 发送弹窗报警信号
                self.alert_signal.emit("流程异常", "检测到工件堆积！\n请立即处理！")
                state_changed = True

        # 0. 检查是否有未写入的步骤 (NG逻辑)
        for cls_id in detected_classes:
            if cls_id >= len(self.sop_steps):
                if not self.current_workpiece_ng:
                    self.current_workpiece_ng = True
                    self.log_message.emit(f"未知步骤 {cls_id}", "检测到异常步骤 - 标记NG", current_frame_idx)

        # 1. 处理当前检测到的步骤
        for cls_id in detected_classes:
            if 0 <= cls_id < len(self.sop_steps):
                # 更新最后检测时间
                self.step_last_seen[cls_id] = current_time
                
                # 如果该步骤没有在检测中，标记为检测中，并记录开始时间
                if not self.step_detecting[cls_id]:
                    self.step_detecting[cls_id] = True
                    self.step_start_times[cls_id] = current_time # 记录开始时间
                    self.log_message.emit(self.sop_steps[cls_id], "检测中...", current_frame_idx)
                    
                state_changed = True
                
                # 实时更新截图
                frame_rgb = cv2.cvtColor(frame_copy, cv2.COLOR_BGR2RGB)
                self.step_screenshot.emit(cls_id, frame_rgb)
        
        # 2. 检查哪些步骤消失了（完成逻辑）
        for cls_id in range(len(self.sop_steps)):
            if self.step_detecting[cls_id]:
                if cls_id in self.step_last_seen:
                    time_since_last_seen = current_time - self.step_last_seen[cls_id]
                    
                    if time_since_last_seen > self.disappear_threshold:
                        # 目标已消失，确认完成
                        self.step_detecting[cls_id] = False
                        self.round_step_flags[cls_id] = True # 标记本轮该步骤已完成
                        
                        # --- NEW: 更新核心步骤状态 & 计数 ---
                        step_name = self.sop_steps[cls_id]
                        if step_name == '测硬度': # ID 0
                            self.core_steps_mask[0] = True
                        elif step_name == '扫码': # ID 1
                            self.core_steps_mask[1] = True
                            # 扫码完成算一次操作循环
                            self.cycle_scan_count += 1
                        elif step_name == '下工件': # ID 4
                            self.core_steps_mask[2] = True
                        # -----------------------------------------
                        
                        # PT计算
                        start_t = self.step_start_times.get(cls_id, current_time - 1.0)
                        actual_end_time = self.step_last_seen.get(cls_id, current_time)
                        pt_val = max(0.1, actual_end_time - start_t)
                        self.step_pt_times[cls_id] = f"{pt_val:.1f}s"
                        
                        # 验证逻辑
                        is_step_ok = True
                        step_msg = "完成"
                        
                        # 如果已经被标记为NG（例如位置挪动），则保持NG
                        if self.step_ng[cls_id]:
                            is_step_ok = False
                            step_msg = "NG (已标记)"
                        
                        if is_step_ok:
                            # 累加逻辑
                            self.step_counts[cls_id] += 1
                            self.log_message.emit(self.sop_steps[cls_id], f"{step_msg} (PT: {pt_val:.1f}s)", current_frame_idx)
                            self.stats['ok_count'] += 1
                        else:
                            self.log_message.emit(self.sop_steps[cls_id], step_msg, current_frame_idx)
                            
                        state_changed = True
                        
                        # 检查触发结算点：当“下工件”完成时
                        if step_name == '下工件': 
                            self.handle_round_completion(current_time, current_frame_idx)

        # 3. 更新UI状态
        if state_changed:
            self.update_defect_rate()
            self.sop_update.emit(
                self.step_counts.copy(),
                self.step_detecting.copy(),
                self.step_ng.copy(),
                self.step_pt_times.copy(),
                False
            )
    
    def handle_round_completion(self, current_time, current_frame_idx):
        """处理一轮完成 (新逻辑：根据工件堆积和操作次数判定)"""
        
        outcome_type = "NG" # Normal, Recheck, NG
        ng_reasons = []
        
        # 1. 工件堆积判定 (最高优先级 NG)
        if self.pile_up_detected:
            outcome_type = "NG"
            ng_reasons.append("工件堆积")
        
        # 2. 计数判定
        else:
            # 正常: 1次测扫 (cycle_scan_count == 1)
            # 复检: 2次测扫 (cycle_scan_count == 2)
            # NG: >=3次测扫 (连续漏下) 或 0次 (跳步/只下工件)
            
            if self.cycle_scan_count == 1:
                outcome_type = "Normal"
            elif self.cycle_scan_count == 2:
                # 复检工件 (合格)
                outcome_type = "Recheck"
            elif self.cycle_scan_count >= 3:
                outcome_type = "NG"
                ng_reasons.append(f"连续未下工件 ({self.cycle_scan_count}次测扫)")
            else: # == 0
                outcome_type = "NG"
                ng_reasons.append("跳步 (未扫码直接下工件)")
            
        # 3. 结算
        self.stats['total_rounds'] += 1
        
        # 计算CT
        ct = current_time - self.round_start_time
        self.ct_history.append(ct)
        if len(self.ct_history) > 0:
            self.stats['avg_ct'] = sum(self.ct_history) / len(self.ct_history)
        self.round_start_time = current_time # 重置轮次开始时间
        
        if outcome_type == "Normal":
            self.stats['qualified_rounds'] += 1
            self.log_message.emit("系统判定", f"正常合格", current_frame_idx)
            
        elif outcome_type == "Recheck":
            self.stats['recheck_rounds'] += 1
            self.log_message.emit("系统判定", f"复检工件 (补测通过)", current_frame_idx)
            
        else: # NG
            self.stats['ng_rounds'] += 1
            self.stats['ng_count'] += 1 # 兼容
            self.current_workpiece_ng = True
            reason_str = " | ".join(ng_reasons)
            self.log_message.emit("流程异常", f"本轮NG: {reason_str}", current_frame_idx)
            
            # 触发报警 (如果是连续漏下或堆积导致的)
            self.alert_signal.emit("流程NG", f"判定为不合格品\n{reason_str}")
            
        # 5. 重置本轮状态
        self.current_workpiece_ng = False
        self.pile_up_detected = False
        self.cycle_scan_count = 0
        self.round_step_flags = [False] * len(self.sop_steps) 
        self.step_ng = [False] * len(self.sop_steps)
        self.step_movement_ng_flags = [False] * len(self.sop_steps)
        self.core_steps_mask = [False, False, False]
        # self.unused_laser_count = 0 # 激光逻辑暂时废弃
        
        self.log_message.emit("工艺卡片", f"第{self.stats['total_rounds']}轮结束 (CT: {ct:.1f}s)", current_frame_idx)
        self.update_defect_rate()
        
        # 发送更新
        self.sop_update.emit(
            self.step_counts.copy(),
            self.step_detecting.copy(),
            self.step_ng.copy(),
            self.step_pt_times.copy(),
            True
        )

    def update_defect_rate(self):
        """更新不良率: NG轮数 / 总轮数"""
        total = self.stats['total_rounds']
        ng_rounds = self.stats.get('ng_rounds', 0)
        
        if total > 0:
            self.stats['defect_rate'] = (ng_rounds / total) * 100
        else:
            self.stats['defect_rate'] = 0.0

    def clear_stats(self):
        """清空统计数据 (保持运行)"""
        self.stats = {
            'total_rounds': 0,
            'qualified_rounds': 0,
            'recheck_rounds': 0,
            'ng_rounds': 0,
            'ok_count': 0,
            'ng_count': 0,
            'defect_rate': 0.0,
            'pt_count': 0,
            'avg_ct': 0.0
        }
        self.ct_history.clear()
        self.reset_sop() # 同时重置当前SOP流程
        self.statistics_update.emit(self.stats.copy())
    
    def reset_sop(self):
        """重置SOP流程"""
        self.step_counts = [0] * len(self.sop_steps)
        self.step_detecting = [False] * len(self.sop_steps)
        self.step_ng = [False] * len(self.sop_steps)
        self.step_pt_times = [""] * len(self.sop_steps)
        self.step_last_seen.clear()
        self.step_start_times.clear()
        self.all_completed = False
        self.current_workpiece_ng = False
        self.round_step_flags = [False] * len(self.sop_steps) # 重置本轮标志
        self.step_movement_ng_flags = [False] * len(self.sop_steps) # 重置移动NG标志
        self.core_steps_mask = [False, False, False]
        # self.unused_laser_count = 0
        self.round_start_time = time.time()
        
        # NEWcycle_scan_count = 0 
        self.pile_up_detected = False
        self.last_pile_up_time = 0
        
        self.sop_update.emit(
            self.step_counts.copy(),
            self.step_detecting.copy(),
            self.step_ng.copy(),
            self.step_pt_times.copy(),
            False
        )
        self.log_message.emit("系统", "开始新一轮检测", -1)
        self.clear_screenshots.emit()
    
    def stop(self):
        self.running = False
        self.wait()
    
    def update_params(self, conf, iou):
        self.conf = conf
        self.iou = iou
        
    def update_step_confs(self, confs):
        self.step_confs = confs.copy()

class ConfidenceDialog(QDialog):
    def __init__(self, steps, current_confs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("每步骤置信度设置")
        self.resize(400, 500)
        self.steps = steps
        self.confs = current_confs.copy()
        self.spin_boxes = {}
        self.setStyleSheet("""
            QDialog { background-color: #020b14; color: #00f3ff; }
            QLabel { color: #00f3ff; font-size: 14px; }
            QDoubleSpinBox { 
                background-color: #0a1a2a; 
                color: #ffffff; 
                border: 1px solid #004455;
                padding: 5px;
            }
            QPushButton {
                background-color: #0055aa;
                color: white;
                border: 1px solid #0077cc;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #0066cc; }
        """)
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setStyleSheet("background-color: transparent;") 
        form_layout = QGridLayout(content)
        
        for i, step in enumerate(self.steps):
            label = QLabel(f"{i}. {step}") # Step index starts at 0 in list, display as is or +1? User code uses 0-based in some places but displays "1." in SOP list.
            # But the SOP_STEPS are strings. The ID is the index.
            spin = QDoubleSpinBox()
            spin.setRange(0.01, 1.00)
            spin.setSingleStep(0.05)
            spin.setValue(self.confs.get(i, 0.25))
            
            form_layout.addWidget(label, i, 0)
            form_layout.addWidget(spin, i, 1)
            self.spin_boxes[i] = spin
            
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        btn_box = QHBoxLayout()
        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)
        
    def get_confs(self):
        new_confs = {}
        for i, spin in self.spin_boxes.items():
            new_confs[i] = spin.value()
        return new_confs

# ----------------- 主窗口 -----------------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("天军科技 AI-视觉AI行为防错检测系统")
        self.resize(1200, 650) # 调整窗口大小，减小高度以适应屏幕
        
        self.worker = None
        self.current_model = DEFAULT_MODEL
        self.current_source = DEFAULT_VIDEO
        self.current_source_type = None
        self.rotation_angle = 0  # 0, 90, 180, 270
        self.sop_steps = list(SOP_STEPS) # 初始化SOP步骤
        
        self.step_screenshot_labels = {}
        self.step_labels = [] # 存储步骤标签
        
        # Initialize per-step confidence thresholds with default 0.25
        self.step_conf_thresholds = {i: 0.25 for i in range(len(self.sop_steps))}
        
        self.setStyleSheet(INDUSTRIAL_STYLE)
        self.init_ui()
        
        # 加载配置
        self.load_settings()
        
        # 时钟更新
        self.clock_timer = QTimer()
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.update_clock()
        
        # 系统监控定时器
        self.sys_timer = QTimer()
        self.sys_timer.timeout.connect(self.update_system_stats)
        self.sys_timer.start(2000)  # 每2秒更新一次

        # 启动动画
        self.setWindowOpacity(0)
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(1500)
        self.anim.setStartValue(0)
        self.anim.setEndValue(1)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.start()
    
    def update_system_stats(self):
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        self.cpu_bar.setValue(int(cpu))
        self.ram_bar.setValue(int(ram))

    def closeEvent(self, event: QCloseEvent):
        self.save_settings()
        if self.worker:
            self.worker.stop()
        event.accept()

    def load_settings(self):
        """加载用户配置"""
        settings = QSettings("TianJun", "SOP_System")
        
        # 1. 模型路径
        model_path = settings.value("model_path", DEFAULT_MODEL)
        self.current_model = model_path
        self.le_model.setText(Path(model_path).name)

        # 2. 输入源
        # 注意：摄像头ID (int) 保存后可能会变成字符串，需要处理
        src_type = settings.value("source_type", "")
        src_val = settings.value("source", "")
        
        if src_type:
            self.current_source_type = src_type
            if src_type == 'camera':
                # 尝试转回int
                try: 
                    self.current_source = int(src_val)
                except:
                    self.current_source = 0 # fallback
            else:
                self.current_source = src_val
            
            if self.current_source:
                 self.add_log("系统", f"已恢复上次输入源: {self.current_source}")

        # 3. 参数
        try:
            conf = int(settings.value("conf", 25))
            iou = int(settings.value("iou", 45))
            self.slider_conf.setValue(conf)
            self.slider_iou.setValue(iou)
        except:
            pass
            
        # 4. 分步置信度
        try:
            step_confs_json = settings.value("step_confs", "{}")
            saved_confs = json.loads(step_confs_json)
            # key in json is string, need int for logic
            self.step_conf_thresholds = {int(k): float(v) for k, v in saved_confs.items()}
        except Exception as e:
            print(f"Error loading step confs: {e}")

        # 5. 旋转
        try:
            rot = int(settings.value("rotation", 0))
            self.rotation_angle = rot
            self.video_label.setRotation(rot)
            if self.worker:
                self.worker.set_rotation(rot)
        except:
            pass

    def save_settings(self):
        """保存用户配置"""
        settings = QSettings("TianJun", "SOP_System")
        
        if self.current_model:
            settings.setValue("model_path", self.current_model)
        
        if self.current_source is not None:
            settings.setValue("source", self.current_source)
            settings.setValue("source_type", self.current_source_type)
            
        settings.setValue("conf", self.slider_conf.value())
        settings.setValue("iou", self.slider_iou.value())
        
        # 分步置信度 (dict -> json str)
        settings.setValue("step_confs", json.dumps(self.step_conf_thresholds))
        
        settings.setValue("rotation", self.rotation_angle)
        
        self.add_log("系统", "配置已保存")

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 顶部标题栏
        self.create_header(main_layout)
        
        # 主体区域 (改为上下结构)
        # 上部: 左右控制 + 中间视频 + 右侧统计
        top_content = QFrame()
        top_layout = QHBoxLayout(top_content)
        top_layout.setContentsMargins(10, 10, 10, 5)
        top_layout.setSpacing(10)
        
        # 左侧控制面板
        self.create_left_panel(top_layout)
        
        # 中间视频
        self.create_center_panel(top_layout)
        
        # 右侧统计
        self.create_right_panel(top_layout)
        
        main_layout.addWidget(top_content, stretch=3) # 上部比例 3
        
        # 下部: 工艺卡片
        self.create_bottom_panel(main_layout)
    
    def create_header(self, parent):
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(50)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 5, 20, 5)
        
        lbl_title = QLabel("🏭 天军科技 AI-视觉AI行为防错检测系统")
        lbl_title.setObjectName("Title")
        hl.addWidget(lbl_title)
        hl.addStretch()
        
        self.lbl_clock = QLabel()
        self.lbl_clock.setObjectName("Clock")
        hl.addWidget(self.lbl_clock)
        
        parent.addWidget(header)
    
    def update_clock(self):
        now = QDateTime.currentDateTime().toString("yyyy-MM-dd hh:mm:ss")
        self.lbl_clock.setText(f"📅 {now}")
    
    def create_left_panel(self, parent):
        panel = QWidget()
        panel.setFixedWidth(220) # 收窄左侧以给工艺卡片更多空间
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(5) # 减小间距
        
        # 1. 系统配置
        gb_model = QGroupBox("⚙️ 系统配置")
        vb_model = QVBoxLayout(gb_model)
        self.le_model = QLineEdit(Path(self.current_model).name)
        self.le_model.setReadOnly(True)
        
        hl_model_btns = QHBoxLayout()
        btn_model = QPushButton("📂 模型")
        btn_model.setProperty("class", "BigBtn")
        btn_model.clicked.connect(self.select_model)
        btn_classes = QPushButton("📝 流程")
        btn_classes.setProperty("class", "BigBtn")
        btn_classes.clicked.connect(self.select_classes_file)
        hl_model_btns.addWidget(btn_model)
        hl_model_btns.addWidget(btn_classes)
        
        vb_model.addWidget(self.le_model)
        vb_model.addLayout(hl_model_btns)
        vbox.addWidget(gb_model)
        
        # 2. 输入源
        gb_src = QGroupBox("📹 输入源")
        hl_src = QHBoxLayout(gb_src)
        btn_video = QPushButton("视频")
        btn_video.setProperty("class", "BigBtn")
        btn_video.clicked.connect(self.select_video)
        btn_image = QPushButton("图片")
        btn_image.setProperty("class", "BigBtn")
        btn_image.clicked.connect(self.select_image)
        btn_camera = QPushButton("摄像头")
        btn_camera.setProperty("class", "BigBtn")
        btn_camera.clicked.connect(self.select_camera)
        hl_src.addWidget(btn_video)
        hl_src.addWidget(btn_image)
        hl_src.addWidget(btn_camera)
        vbox.addWidget(gb_src)
        
        # 3. 检测参数
        gb_params = QGroupBox("🎛️ 检测参数")
        vb_params = QVBoxLayout(gb_params)
        vb_params.setSpacing(5) # 紧凑
        
        # 置信度: Label - Slider - Value
        hl_conf = QHBoxLayout()
        hl_conf.addWidget(QLabel("置信度:"))
        self.slider_conf = QSlider(Qt.Horizontal)
        self.slider_conf.setRange(10, 90)
        self.slider_conf.setValue(25)
        self.slider_conf.valueChanged.connect(self.update_params)
        hl_conf.addWidget(self.slider_conf)
        self.lbl_conf_val = QLabel("25%")
        self.lbl_conf_val.setFixedWidth(35)
        hl_conf.addWidget(self.lbl_conf_val)
        vb_params.addLayout(hl_conf)
        
        # 分步置信度按钮
        btn_step_conf = QPushButton("⚙️ 分步设定")
        btn_step_conf.setProperty("class", "ActionBtn")
        btn_step_conf.clicked.connect(self.open_conf_dialog)
        vb_params.addWidget(btn_step_conf)
        
        # IOU: Label - Slider - Value
        hl_iou = QHBoxLayout()
        hl_iou.addWidget(QLabel("IOU:"))
        self.slider_iou = QSlider(Qt.Horizontal)
        self.slider_iou.setRange(10, 90)
        self.slider_iou.setValue(45)
        self.slider_iou.valueChanged.connect(self.update_params)
        hl_iou.addWidget(self.slider_iou)
        self.lbl_iou_val = QLabel("45%")
        self.lbl_iou_val.setFixedWidth(35)
        hl_iou.addWidget(self.lbl_iou_val)
        vb_params.addLayout(hl_iou)
        
        vbox.addWidget(gb_params)
        
        # 4. 视频控制
        gb_rotate = QGroupBox("🎮 视频控制")
        vb_rotate = QVBoxLayout(gb_rotate)
        hl_speed = QHBoxLayout()
        btn_slow = QPushButton("0.5x")
        btn_slow.clicked.connect(lambda: self.set_speed(0.5))
        btn_normal = QPushButton("1.0x")
        btn_normal.clicked.connect(lambda: self.set_speed(1.0))
        btn_fast = QPushButton("2.0x")
        btn_fast.clicked.connect(lambda: self.set_speed(2.0))
        hl_speed.addWidget(btn_slow)
        hl_speed.addWidget(btn_normal)
        hl_speed.addWidget(btn_fast)
        vb_rotate.addLayout(hl_speed)
        
        hl_rotate = QHBoxLayout()
        self.btn_rotate_left = QPushButton("↺ 逆时针")
        self.btn_rotate_left.clicked.connect(self.rotate_left)
        self.btn_rotate_right = QPushButton("↻ 顺时针")
        self.btn_rotate_right.clicked.connect(self.rotate_right)
        hl_rotate.addWidget(self.btn_rotate_left)
        hl_rotate.addWidget(self.btn_rotate_right)
        vb_rotate.addLayout(hl_rotate)
        vbox.addWidget(gb_rotate)
        
        vbox.addStretch() 
        parent.addWidget(panel)
    
    def create_center_panel(self, parent):
        # 中间只放视频，最大化利用空间
        panel = QWidget()
        stack_layout = QGridLayout(panel) # 使用 Grid 使得两个控件重叠
        stack_layout.setContentsMargins(0, 0, 0, 0)
        
        self.video_label = VideoLabel("等待输入信号\nWAITING FOR INPUT")
        stack_layout.addWidget(self.video_label, 0, 0)
        
        # 报警弹窗 (默认隐藏)
        self.alert_overlay = QLabel("⚠️ 异常警告 ⚠️")
        self.alert_overlay.setAlignment(Qt.AlignCenter)
        # 赛博朋克红色警报风格 (美化：半透明黑底 + 霓虹红框)
        self.alert_overlay.setStyleSheet("""
            QLabel {
                background-color: rgba(20, 0, 0, 0.90);
                border: 6px solid #ff0000;
                color: #ff0000;
                font-family: "Microsoft YaHei", "SimHei";
                font-size: 52px;
                font-weight: 900;
                border-radius: 20px;
                padding: 40px;
                min-width: 400px;
                min-height: 200px;
            }
        """)
        self.alert_overlay.hide()
        
        # 报警定时器
        self.alert_timer = QTimer()
        self.alert_timer.setSingleShot(True)
        self.alert_timer.timeout.connect(self.alert_overlay.hide)
        
        # 居中叠加
        stack_layout.addWidget(self.alert_overlay, 0, 0, alignment=Qt.AlignCenter)
        
        parent.addWidget(panel, stretch=1)
        
    def show_alert(self, title, msg):
        """显示报警弹窗"""
        self.alert_overlay.setText(f"⚠️ {title} ⚠️\n\n{msg}")
        self.alert_overlay.show()
        self.alert_overlay.raise_() # 确保在最上层
        self.alert_timer.start(5000) # 显示5秒
    
    def rebuild_sop_ui(self):
        """重建SOP界面 - 底部横向卡片模式"""
        if self.gs_sop.layout():
            old_layout = self.gs_sop.layout()
            while old_layout.count():
                item = old_layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            QWidget().setLayout(old_layout)
        
        gs_layout = QVBoxLayout(self.gs_sop)
        gs_layout.setContentsMargins(5, 15, 5, 5)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff) # 横向滚动，禁止垂直滚动条
        
        scroll_content = QWidget()
        # 改为水平布局
        vl = QHBoxLayout(scroll_content)
        vl.setSpacing(10)
        
        self.step_labels = []
        self.step_screenshot_labels = {}
        
        for i, step_name in enumerate(self.sop_steps):
            # 每个步骤一个垂直卡片容器
            step_container = QFrame()
            if i in IGNORED_STEPS_IDS:
                step_container.setVisible(False)
            
            step_container.setStyleSheet("background-color: rgba(255,255,255,0.05); border-radius: 4px;")
            step_layout = QVBoxLayout(step_container) # 卡片内部垂直
            step_layout.setContentsMargins(5, 5, 5, 5)
            
            # 步骤名称
            lbl = QLabel(f"{i+1}. {step_name}")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setProperty("class", "StepPending")
            lbl.setFixedHeight(30)
            self.step_labels.append(lbl)
            step_layout.addWidget(lbl)
            
            # 截图 (大图)
            screenshot_lbl = QLabel("等待截图")
            screenshot_lbl.setObjectName("StepScreenshot")
            screenshot_lbl.setFixedSize(220, 165) # 更大的预览图
            screenshot_lbl.setAlignment(Qt.AlignCenter)
            screenshot_lbl.setScaledContents(True)
            screenshot_lbl.setStyleSheet("border: 1px solid #444; background: #000;")
            self.step_screenshot_labels[i] = screenshot_lbl
            step_layout.addWidget(screenshot_lbl)
            
            vl.addWidget(step_container)
        
        vl.addStretch()
        scroll.setWidget(scroll_content)
        gs_layout.addWidget(scroll)

    def create_bottom_panel(self, parent_layout):
        """创建底部工艺卡片面板"""
        self.gs_sop = QGroupBox("📋 工艺卡片")
        # 直接添加到父布局 (Main VBox)
        parent_layout.addWidget(self.gs_sop, stretch=1) # 底部占1份
        self.rebuild_sop_ui()

    def create_right_panel(self, parent):
        panel = QWidget()
        panel.setFixedWidth(360) # 放宽右侧，突出工艺卡片
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(5) # 减小间距
        
        # 1. 实时监控 (CPU/RAM)
        gb_sys = QGroupBox("🖥️ 系统状态")
        vb_sys = QVBoxLayout(gb_sys)
        hl_cpu = QHBoxLayout()
        hl_cpu.addWidget(QLabel("CPU"))
        self.cpu_bar = QProgressBar()
        self.cpu_bar.setRange(0, 100)
        self.cpu_bar.setTextVisible(True)
        self.cpu_bar.setFixedHeight(15)
        hl_cpu.addWidget(self.cpu_bar)
        vb_sys.addLayout(hl_cpu)
        hl_ram = QHBoxLayout()
        hl_ram.addWidget(QLabel("RAM"))
        self.ram_bar = QProgressBar()
        self.ram_bar.setRange(0, 100)
        self.ram_bar.setTextVisible(True)
        self.ram_bar.setFixedHeight(15)
        hl_ram.addWidget(self.ram_bar)
        vb_sys.addLayout(hl_ram)
        gb_sys.hide()  # 隐藏系统状态
        vbox.addWidget(gb_sys)

        # 2. 自动放大 (已注释)
        # gb_mag = QGroupBox("🔍 自动放大")
        # vb_mag = QVBoxLayout(gb_mag)
        # self.lbl_magnifier = QLabel("等待检测...")
        # self.lbl_magnifier.setObjectName("Magnifier")
        # self.lbl_magnifier.setFixedSize(230, 130) # 减小高度以节省空间
        # self.lbl_magnifier.setAlignment(Qt.AlignCenter)
        # self.lbl_magnifier.setScaledContents(True)
        # vb_mag.addWidget(self.lbl_magnifier, alignment=Qt.AlignCenter)
        # vbox.addWidget(gb_mag)
        
        # 3. 统计数据
        gb_stats = QGroupBox("📈 统计数据")
        gl_stats = QGridLayout(gb_stats)
        gl_stats.setContentsMargins(5, 5, 5, 5) # 紧凑布局
        
        self.lbl_rounds = QLabel("总 轮 数: 0")
        self.lbl_rounds.setProperty("class", "StatLabel")
        gl_stats.addWidget(self.lbl_rounds, 0, 0, 1, 2)
        
        self.lbl_qualified = QLabel("正常工件: 0")
        self.lbl_qualified.setProperty("class", "StatLabel")
        self.lbl_qualified.setStyleSheet("color: #00ff00;")
        gl_stats.addWidget(self.lbl_qualified, 1, 0)
        
        self.lbl_recheck = QLabel("复检工件: 0")
        self.lbl_recheck.setProperty("class", "StatLabel")
        self.lbl_recheck.setStyleSheet("color: #ffff00;")
        gl_stats.addWidget(self.lbl_recheck, 1, 1)

        self.lbl_ng_rounds = QLabel("NG 工件: 0")
        self.lbl_ng_rounds.setProperty("class", "StatLabel")
        self.lbl_ng_rounds.setStyleSheet("color: #ff0000;")
        gl_stats.addWidget(self.lbl_ng_rounds, 2, 0)
        
        self.lbl_defect = QLabel("不良率: 0.0%")
        self.lbl_defect.setProperty("class", "StatLabel")
        self.lbl_defect.setStyleSheet("color: #ffaa00;")
        gl_stats.addWidget(self.lbl_defect, 2, 1)

        # 详细步骤统计
        self.lbl_ok = QLabel("OK步骤: 0")
        self.lbl_ok.setProperty("class", "StatLabel")
        self.lbl_ok.setStyleSheet("color: #00aa00; font-size: 11px;")
        gl_stats.addWidget(self.lbl_ok, 3, 0)

        self.lbl_ng = QLabel("NG步骤: 0")
        self.lbl_ng.setProperty("class", "StatLabel")
        self.lbl_ng.setStyleSheet("color: #aa0000; font-size: 11px;")
        gl_stats.addWidget(self.lbl_ng, 3, 1)
        
        self.lbl_ct = QLabel("平均CT: 0.0s")
        self.lbl_ct.setProperty("class", "StatLabel")
        gl_stats.addWidget(self.lbl_ct, 4, 0, 1, 2)
        
        vbox.addWidget(gb_stats)

        # 4. 操作日志 (新)
        gl_log = QGroupBox("📝 操作日志")
        vl = QVBoxLayout(gl_log)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["时间", "动作", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellClicked.connect(self.on_log_clicked)
        vl.addWidget(self.table)
        vbox.addWidget(gl_log, stretch=10) # 给日志较大权重，让其占用中间空间
        
        # 5. 控制按钮
        self.btn_start = QPushButton("▶ 启动")
        self.btn_start.setObjectName("BtnStart")
        self.btn_start.setProperty("class", "ActionBtn")
        self.btn_start.setFixedHeight(40)
        self.btn_start.clicked.connect(self.toggle_start)
        
        hl_ctrl = QHBoxLayout()
        self.btn_reset_sop = QPushButton("🔄 重置")
        self.btn_reset_sop.setObjectName("BtnReset")
        self.btn_reset_sop.setProperty("class", "ActionBtn")
        self.btn_reset_sop.setFixedHeight(32)
        self.btn_reset_sop.clicked.connect(self.reset_sop_flow)
        
        self.btn_clear = QPushButton("🗑 清空")
        self.btn_clear.setObjectName("BtnClear")
        self.btn_clear.setProperty("class", "ActionBtn")
        self.btn_clear.setFixedHeight(32)
        self.btn_clear.clicked.connect(self.clear_results)
        
        hl_ctrl.addWidget(self.btn_reset_sop)
        hl_ctrl.addWidget(self.btn_clear)
        
        # 新增：导出报表
        btn_export = QPushButton("📊 导出报表")
        btn_export.setObjectName("BtnExport")
        btn_export.setProperty("class", "ActionBtn")
        btn_export.setFixedHeight(32)
        btn_export.clicked.connect(self.export_report)
        
        vbox.addWidget(self.btn_start)
        vbox.addLayout(hl_ctrl)
        vbox.addWidget(btn_export)
        
        # vbox.addStretch() # 移除顶上去的填充，由日志列表填充空间
        
        parent.addWidget(panel)
    
    def open_conf_dialog(self):
        dlg = ConfidenceDialog(self.sop_steps, self.step_conf_thresholds, self)
        if dlg.exec():
            self.step_conf_thresholds = dlg.get_confs()
            if self.worker:
                self.worker.update_step_confs(self.step_conf_thresholds)

    def update_params(self):
        c = self.slider_conf.value()
        i = self.slider_iou.value()
        self.lbl_conf_val.setText(f"{c}%")
        self.lbl_iou_val.setText(f"{i}%")
        if self.worker:
            self.worker.update_params(c/100.0, i/100.0)
            self.worker.update_step_confs(self.step_conf_thresholds)
    
    def select_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择检测模型", "", "Model (*.pt *.engine *.onnx)")
        if path:
            self.current_model = path
            self.le_model.setText(Path(path).name)
            if self.worker:
                QMessageBox.information(self, "提示", "模型已更新，请重启检测。")
    
    def select_classes_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择流程文件", "", "Text Files (*.txt);;All Files (*)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                if lines:
                    self.sop_steps = lines
                    self.rebuild_sop_ui()
                    self.add_log("系统", f"已加载流程: {len(lines)}步")
                    if self.worker:
                        QMessageBox.information(self, "提示", "流程已更新，请重启检测。")
                else:
                    QMessageBox.warning(self, "错误", "文件为空")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法读取文件: {e}")

    def select_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Videos (*.mp4 *.avi)")
        if path:
            self.current_source = path
            self.current_source_type = 'video'
            self.stop_worker()
            QMessageBox.information(self, "就绪", f"已加载: {Path(path).name}")
    
    def select_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "Images (*.jpg *.png *.bmp)")
        if path:
            self.current_source = path
            self.current_source_type = 'image'
            self.stop_worker()
            QMessageBox.information(self, "就绪", f"已加载: {Path(path).name}")
    
    def select_camera(self):
        # 自动检测摄像头列表
        cameras = []
        if HAS_MULTIMEDIA:
            devices = QMediaDevices.videoInputs()
            for i, device in enumerate(devices):
                cameras.append(f"{i}: {device.description()}")
        
        # 如果没有检测到或没安装QtMultimedia，回退到探测前3个端口
        if not cameras:
            for i in range(3):
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    cameras.append(f"摄像头 {i}")
                    cap.release()
        
        if not cameras:
             QMessageBox.warning(self, "未发现设备", "未检测到可用的摄像头设备。")
             return

        item, ok = QInputDialog.getItem(self, "选择摄像头", "检测到的设备:", cameras, 0, False)
        if ok and item:
            # 解析索引 "0: xxx" -> 0
            idx = int(item.split(":")[0]) if ":" in item else int(item.split(" ")[1])
            self.current_source = idx
            self.current_source_type = 'camera'
            self.stop_worker()
            QMessageBox.information(self, "就绪", f"选择了摄像头: {item}")
    
    def toggle_start(self):
        if not self.worker:
            if self.current_source is None or self.current_source_type is None:
                QMessageBox.warning(self, "未选择输入", "请先选择输入源。")
                return
            if not self.current_model:
                QMessageBox.warning(self, "未选择模型", "请先选择检测模型。")
                return
            
            self.worker = InferenceWorker(
                self.current_model,
                self.current_source,
                self.sop_steps,
                self.current_source_type
            )
            self.worker.frame_update.connect(self.show_frame)
            self.worker.magnifier_update.connect(self.show_magnifier)
            self.worker.step_screenshot.connect(self.show_step_screenshot)
            self.worker.sop_update.connect(self.up_sop_ui)
            self.worker.clear_screenshots.connect(self.clear_step_screenshots)
            self.worker.statistics_update.connect(self.update_statistics)
            self.worker.log_message.connect(self.add_log)
            self.worker.alert_signal.connect(self.show_alert) # 新增：连接报警信号
            self.worker.finished_sig.connect(self.stop_worker)
            self.update_params()
            self.worker.start()
            
            self.btn_start.setText("⏸ 待机")
            self.btn_start.setProperty("state", "paused")
            self.btn_start.style().unpolish(self.btn_start)
            self.btn_start.style().polish(self.btn_start)
        else:
            if self.worker.paused:
                self.worker.set_pause(False)
                self.btn_start.setText("⏸ 待机")
                self.btn_start.setProperty("state", "paused")
                self.btn_start.style().unpolish(self.btn_start)
                self.btn_start.style().polish(self.btn_start)
            else:
                self.worker.set_pause(True)
                self.btn_start.setText("▶ 继续")
                self.btn_start.setProperty("state", "normal") # 恢复蓝色
                self.btn_start.style().unpolish(self.btn_start)
                self.btn_start.style().polish(self.btn_start)
    
    def stop_worker(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.btn_start.setText("▶ 启动")
        self.btn_start.setProperty("state", "normal")
        self.btn_start.style().unpolish(self.btn_start)
        self.btn_start.style().polish(self.btn_start)
    
    def reset_sop_flow(self):
        if self.worker:
            self.worker.reset_sop()
        else:
            self.up_sop_ui([0]*len(self.sop_steps), [False]*len(self.sop_steps), [False]*len(self.sop_steps), [""]*len(self.sop_steps), False)
            self.clear_step_screenshots()  # 清空截图
        self.add_log("系统", "流程重置")
    
    def clear_results(self):
        # self.stop_worker() # 不停止Worker，仅清空数据
        
        # 1. 清空日志 UI
        self.table.setRowCount(0)
        
        # 2. 重置 Worker 统计 (如果正在运行)
        if self.worker:
            self.worker.clear_stats()
        else:
            # 如果没有运行，手动重置界面上的 SOP 和 统计
            self.reset_sop_flow()
            self.clear_step_screenshots()
            
            # 手动重置统计 Labels
            self.lbl_rounds.setText("总 轮 数: 0")
            self.lbl_qualified.setText("正常工件: 0")
            self.lbl_recheck.setText("复检工件: 0")
            self.lbl_ng_rounds.setText("NG 工件: 0")
            self.lbl_ct.setText("平均CT: 0.0s")
            self.lbl_ok.setText("OK步骤: 0")
            self.lbl_ng.setText("NG步骤: 0")
            self.lbl_defect.setText("不良率: 0.0%")
            
        self.add_log("系统", "统计数据已清空 (视频保持)")
    
    def clear_step_screenshots(self):
        """清空所有步骤的截图"""
        for lbl in self.step_screenshot_labels.values():
            lbl.clear()
            lbl.setText("等待截图...")
        self.add_log("系统", "清空所有步骤截图")
    
    def show_frame(self, img):
        h, w, c = img.shape
        qimg = QImage(img.data, w, h, c*w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        self.video_label.setPixmap(pixmap)
    
    def show_magnifier(self, img):
        if not hasattr(self, 'lbl_magnifier'):
            return
        h, w, c = img.shape
        qimg = QImage(img.data, w, h, c*w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        self.lbl_magnifier.setPixmap(pixmap)
    
    def show_step_screenshot(self, step_index, img):
        """显示步骤截图 - 实时更新"""
        if step_index in self.step_screenshot_labels:
            h, w, c = img.shape
            qimg = QImage(img.data, w, h, c*w, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg)
            self.step_screenshot_labels[step_index].setPixmap(pixmap)
    

    
    def update_statistics(self, stats):
        """更新统计数据"""
        self.lbl_rounds.setText(f"总 轮 数: {stats['total_rounds']}")
        self.lbl_qualified.setText(f"正常工件: {stats['qualified_rounds']}")
        self.lbl_recheck.setText(f"复检工件: {stats.get('recheck_rounds', 0)}")
        self.lbl_ng_rounds.setText(f"NG 工件: {stats.get('ng_rounds', 0)}")
        
        self.lbl_ct.setText(f"平均CT: {stats.get('avg_ct', 0):.1f}s")
        self.lbl_ok.setText(f"OK步骤: {stats['ok_count']}")
        self.lbl_ng.setText(f"NG步骤: {stats['ng_count']}")
        self.lbl_defect.setText(f"不良率: {stats['defect_rate']:.1f}%")
    
    def up_sop_ui(self, counts, detecting_list, ng_list, pt_times, all_done):
        """更新SOP界面显示"""
        for i in range(len(self.sop_steps)):
            if i >= len(self.step_labels): break
            
            lbl = self.step_labels[i]
            step_name = self.sop_steps[i]
            count = counts[i]
            pt = pt_times[i]
            
            # 构建显示文本
            display_text = f"{i+1}. {step_name}"
            if count > 0:
                display_text += f" [{count}]"
            if pt:
                display_text += f" PT:{pt}"
            
            if ng_list[i]:
                # NG状态
                if "StepNG" not in lbl.property("class"):
                    lbl.setProperty("class", "StepNG")
                    lbl.style().unpolish(lbl)
                    lbl.style().polish(lbl)
                lbl.setText(f"❌ {display_text}")
            elif count > 0: # 完成状态 (累加数>0)
                if "StepDone" not in lbl.property("class"):
                    lbl.setProperty("class", "StepDone")
                    lbl.style().unpolish(lbl)
                    lbl.style().polish(lbl)
                lbl.setText(f"✅ {display_text}")
            elif detecting_list[i]:
                # 检测中状态
                if "StepDetecting" not in lbl.property("class"):
                    lbl.setProperty("class", "StepDetecting")
                    lbl.style().unpolish(lbl)
                    lbl.style().polish(lbl)
                lbl.setText(f"🔵 {display_text}")
            else:
                # 待执行状态
                if "StepPending" not in lbl.property("class"):
                    lbl.setProperty("class", "StepPending")
                    lbl.style().unpolish(lbl)
                    lbl.style().polish(lbl)
                lbl.setText(display_text)
    
    def add_log(self, action, status, frame_index=-1):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(
            datetime.datetime.now().strftime("%H:%M:%S")
        ))
        self.table.setItem(r, 1, QTableWidgetItem(action))
        
        it = QTableWidgetItem(status)
        if status == "完成" or "补充" in status:
            it.setForeground(QColor("#ADFF2F"))
        elif "检测中" in status:
            it.setForeground(QColor("#A0E6FF"))
        elif "NG" in status or "跳过" in status:
            it.setForeground(QColor("#ff0000"))
        else:
            it.setForeground(QColor("#00f0ff"))
        
        # 存储帧索引到UserRole
        it.setData(Qt.UserRole, frame_index)
        
        self.table.setItem(r, 2, it)
        self.table.scrollToBottom()
    
    def on_log_clicked(self, row, col):
        """点击日志跳转到对应帧，并显示完整信息"""
        # 获取该行的完整信息
        time_text = self.table.item(row, 0).text()
        action_text = self.table.item(row, 1).text()
        status_item = self.table.item(row, 2)
        status_text = status_item.text() if status_item else ""
        
        # 弹窗内容
        msg_content = (
            f"🕒 时间: {time_text}\n"
            f"🎬 动作: {action_text}\n"
            f"📝 状态详情:\n{status_text}\n"
        )

        item = self.table.item(row, 2)
        if item:
            frame_index = item.data(Qt.UserRole)
            if frame_index is not None and frame_index >= 0:
                msg_content += f"\n----------------\n🎞️ 已跳转到帧: {frame_index} (自动暂停)"
                
                if self.worker:
                    self.worker.seek(frame_index)
                    self.worker.set_pause(True)
                    self.btn_start.setText("▶ 继续")
                    # 刷新按钮样式
                    self.btn_start.setProperty("state", "paused")
                    self.btn_start.style().unpolish(self.btn_start)
                    self.btn_start.style().polish(self.btn_start)
            
            QMessageBox.information(self, "日志详情 & 跳转反馈", msg_content)
    
    def set_speed(self, speed):
        if self.worker:
            self.worker.set_speed(speed)
            self.add_log("系统", f"播放速度: {speed}x", -1)

    def rotate_left(self):
        """逆时针旋转90度"""
        self.rotation_angle = (self.rotation_angle - 90) % 360
        if self.worker:
            self.worker.set_rotation(self.rotation_angle)
        self.video_label.setRotation(self.rotation_angle)
        self.add_log("系统", f"视频旋转: {self.rotation_angle}°")
    
    def rotate_right(self):
        """顺时针旋转90度"""
        self.rotation_angle = (self.rotation_angle + 90) % 360
        if self.worker:
            self.worker.set_rotation(self.rotation_angle)
        self.video_label.setRotation(self.rotation_angle)
        self.add_log("系统", f"视频旋转: {self.rotation_angle}°")

    def export_report(self):
        """导出报表"""
        path, _ = QFileDialog.getSaveFileName(self, "导出报表", "", "CSV Files (*.csv)")
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    # 写入统计信息
                    writer.writerow(["统计数据"])
                    writer.writerow(["总轮数", self.lbl_rounds.text().split(": ")[1]])
                    writer.writerow(["正常工件", self.lbl_qualified.text().split(": ")[1]])
                    writer.writerow(["复检工件", self.lbl_recheck.text().split(": ")[1]])
                    writer.writerow(["NG工件", self.lbl_ng_rounds.text().split(": ")[1]])
                    writer.writerow(["平均CT", self.lbl_ct.text().split(": ")[1]])
                    writer.writerow(["不良率", self.lbl_defect.text().split(": ")[1]])
                    writer.writerow([])
                    
                    # 写入日志
                    writer.writerow(["操作日志"])
                    writer.writerow(["时间", "动作", "状态"])
                    for r in range(self.table.rowCount()):
                        t = self.table.item(r, 0).text()
                        a = self.table.item(r, 1).text()
                        s = self.table.item(r, 2).text()
                        writer.writerow([t, a, s])
                
                QMessageBox.information(self, "成功", f"报表已导出至: {path}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出失败: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    w = MainWindow()
    w.show() # 取消最大化
    sys.exit(app.exec())
