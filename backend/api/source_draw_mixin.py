"""绘制框 + Kalman 滤波 (v2.7.16 P6 阶段一第十三刀)。

把 5 个绘图/滤波方法集中到一处 (合计 ~215 行):
  _apply_kalman_filter  : 对 detections 应用 Kalman 平滑 (64L)
  update_kalman_params  : 运行时调整 Kalman 噪声参数 (29L)
  _draw_box_simple      : 简化版无文字框线 (48L)
  _get_chinese_font     : 加载中文字体 PIL.ImageFont (20L)
  _draw_box             : 主绘图函数, 含中文 label / 置信度 / 角标 (56L)

依赖宿主 (VideoSourceManager):
  - 状态: kalman_trackers / kalman_process_noise / kalman_measurement_noise /
          chinese_font_path / etc.
"""
import time
import numpy as np
import cv2

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None


class DrawMixin:
    def _apply_kalman_filter(self, detections):
        """
        对检测结果应用卡尔曼滤波
        
        Args:
            detections: 原始检测结果列表
            
        Returns:
            滤波后的检测结果列表
        """
        if not self._kalman_enabled:
            return detections
        
        current_labels = set()
        smoothed = []
        
        for det in detections:
            label = det['label']
            current_labels.add(label)
            
            x, y, w, h = det['x'], det['y'], det['w'], det['h']
            measurement = [x, y, w, h]
            
            if label not in self._kalman_filters:
                # 新目标，创建滤波器
                self._kalman_filters[label] = KalmanFilter2D(
                    measurement,
                    process_noise=self._kalman_process_noise,
                    measurement_noise=self._kalman_measurement_noise
                )
                smoothed_pos = measurement
            else:
                # 已有目标，更新滤波器
                kf = self._kalman_filters[label]
                kf.predict()
                smoothed_pos = kf.update(measurement)
            
            # 重置消失计数
            self._detection_missing_frames[label] = 0
            
            # 创建平滑后的检测结果
            smoothed_det = det.copy()
            smoothed_det['x'] = float(smoothed_pos[0])
            smoothed_det['y'] = float(smoothed_pos[1])
            smoothed_det['w'] = float(smoothed_pos[2])
            smoothed_det['h'] = float(smoothed_pos[3])
            smoothed.append(smoothed_det)
        
        # 处理消失的目标
        labels_to_remove = []
        for label in list(self._kalman_filters.keys()):
            if label not in current_labels:
                self._detection_missing_frames[label] = self._detection_missing_frames.get(label, 0) + 1
                if self._detection_missing_frames[label] >= self._max_missing_frames:
                    labels_to_remove.append(label)
        
        # 移除长时间消失的目标的滤波器
        for label in labels_to_remove:
            del self._kalman_filters[label]
            if label in self._detection_missing_frames:
                del self._detection_missing_frames[label]
        
        return smoothed
    
    def update_kalman_params(self, process_noise=None, measurement_noise=None, enabled=None, max_missing_frames=None):
        """
        更新卡尔曼滤波参数
        
        Args:
            process_noise: 过程噪声 Q (0.001-0.5, 默认0.03)
                          越小 → 预测更平滑，对快速变化响应慢
                          越大 → 对快速变化响应快，但更抖动
            measurement_noise: 观测噪声 R (0.01-1.0, 默认0.1)
                              越小 → 更信任观测值，更抖动
                              越大 → 更平滑，但对快速变化响应慢
            enabled: 是否启用滤波
            max_missing_frames: 目标消失多少帧后移除滤波器
        """
        if process_noise is not None:
            self._kalman_process_noise = max(0.001, min(0.5, process_noise))
        if measurement_noise is not None:
            self._kalman_measurement_noise = max(0.01, min(1.0, measurement_noise))
        if enabled is not None:
            self._kalman_enabled = enabled
        if max_missing_frames is not None:
            self._max_missing_frames = max(1, min(30, max_missing_frames))
        
        # 清除现有滤波器，使用新参数重建
        self._kalman_filters.clear()
        self._detection_missing_frames.clear()
        
        print(f"[卡尔曼滤波] 参数更新: enabled={self._kalman_enabled}, Q={self._kalman_process_noise}, R={self._kalman_measurement_noise}, max_missing={self._max_missing_frames}")
    
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
    
