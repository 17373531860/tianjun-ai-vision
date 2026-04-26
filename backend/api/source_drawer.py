"""Drawer 组件 (v2.7.16 P7 阶段一第一刀, 组合优于继承)。

把原 DrawMixin 从"is-a 继承"重构为"has-a 组合":
  - Drawer 类自持 kalman 滤波 + 中文字体缓存 + 检测平滑等所有相关状态
  - VideoSourceManager 通过 self.drawer = Drawer() 持有
  - 通过 __getattr__ 兼容层让历史调用 self._kalman_enabled / self._draw_box(...)
    透明转发到 self.drawer, 现有调用代码无需修改

迁移的状态 (从 VSM.__init__ 搬走):
  _kalman_filters, _kalman_enabled, _kalman_process_noise, _kalman_measurement_noise,
  _detection_history, _detection_missing_frames, _max_missing_frames

公共 API (5 个方法 + 1 个清理):
  apply_kalman_filter(detections)        : 对 detections 应用 Kalman 平滑
  update_kalman_params(...)               : 运行时调整 Kalman 噪声参数
  draw_box(img, x1,y1,x2,y2, label, conf): 主绘图 (赛博朋克风格 + 中文)
  draw_box_simple(img, ...)              : 简化版无角标
  get_chinese_font(size)                 : 加载/缓存中文 PIL 字体
  clear_filters()                        : 清空 kalman 滤波器 + missing 计数
"""
import time
import numpy as np
import cv2

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None


# 来自 source.py 的内部 KalmanFilter2D, 通过 lazy import 避免循环引用
def _get_kalman_filter_cls():
    from backend.api.source import KalmanFilter2D
    return KalmanFilter2D


class Drawer:
    """绘图 + Kalman 滤波组件, 自持状态."""

    # 内部下划线名称是为了让 VSM.__getattr__ 透明转发, 兼容历史调用
    def __init__(self):
        self._kalman_filters = {}
        self._kalman_enabled = False
        self._kalman_process_noise = 0.03
        self._kalman_measurement_noise = 0.1
        self._detection_history = {}
        self._detection_missing_frames = {}
        self._max_missing_frames = 5
        # 中文字体缓存 (按 size), 避免每次重新探 7 条路径
        self._font_cache = {}

    # ===== 状态清理 =====
    def clear_filters(self):
        self._kalman_filters.clear()
        self._detection_missing_frames.clear()

    # ===== Kalman 滤波 =====
    def apply_kalman_filter(self, detections):
        if not self._kalman_enabled:
            return detections

        KalmanFilter2D = _get_kalman_filter_cls()
        current_labels = set()
        smoothed = []

        for det in detections:
            label = det['label']
            current_labels.add(label)
            x, y, w, h = det['x'], det['y'], det['w'], det['h']
            measurement = [x, y, w, h]

            if label not in self._kalman_filters:
                self._kalman_filters[label] = KalmanFilter2D(
                    measurement,
                    process_noise=self._kalman_process_noise,
                    measurement_noise=self._kalman_measurement_noise,
                )
                smoothed_pos = measurement
            else:
                kf = self._kalman_filters[label]
                kf.predict()
                smoothed_pos = kf.update(measurement)

            self._detection_missing_frames[label] = 0

            smoothed_det = det.copy()
            smoothed_det['x'] = float(smoothed_pos[0])
            smoothed_det['y'] = float(smoothed_pos[1])
            smoothed_det['w'] = float(smoothed_pos[2])
            smoothed_det['h'] = float(smoothed_pos[3])
            smoothed.append(smoothed_det)

        labels_to_remove = []
        for label in list(self._kalman_filters.keys()):
            if label not in current_labels:
                self._detection_missing_frames[label] = self._detection_missing_frames.get(label, 0) + 1
                if self._detection_missing_frames[label] >= self._max_missing_frames:
                    labels_to_remove.append(label)

        for label in labels_to_remove:
            del self._kalman_filters[label]
            self._detection_missing_frames.pop(label, None)

        return smoothed

    def update_kalman_params(self, process_noise=None, measurement_noise=None,
                              enabled=None, max_missing_frames=None):
        if process_noise is not None:
            self._kalman_process_noise = max(0.001, min(0.5, process_noise))
        if measurement_noise is not None:
            self._kalman_measurement_noise = max(0.01, min(1.0, measurement_noise))
        if enabled is not None:
            self._kalman_enabled = enabled
        if max_missing_frames is not None:
            self._max_missing_frames = max(1, min(30, max_missing_frames))

        self._kalman_filters.clear()
        self._detection_missing_frames.clear()

        print(f"[卡尔曼滤波] 参数更新: enabled={self._kalman_enabled}, "
              f"Q={self._kalman_process_noise}, R={self._kalman_measurement_noise}, "
              f"max_missing={self._max_missing_frames}")

    # ===== 中文字体 (带缓存) =====
    def get_chinese_font(self, size=20):
        if size in self._font_cache:
            return self._font_cache[size]

        font_paths = [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "simhei.ttf",
        ]
        for path in font_paths:
            try:
                font = ImageFont.truetype(path, size)
                self._font_cache[size] = font
                return font
            except Exception:
                continue

        font = ImageFont.load_default()
        self._font_cache[size] = font
        return font

    # ===== 绘图 =====
    def draw_box_simple(self, img, x1, y1, x2, y2, label, conf):
        color_bgr = (0, 255, 0)
        color_rgb = (0, 255, 0)
        thickness = 2

        cv2.rectangle(img, (x1, y1), (x2, y2), color_bgr, thickness)

        try:
            img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(img_pil)
            font = self.get_chinese_font(18)

            text = f"{label} {int(conf * 100)}%"

            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except Exception:
                text_w, text_h = 100, 20

            bg_y1 = max(0, y1 - text_h - 8)
            bg_y2 = y1
            bg_x1 = x1
            bg_x2 = x1 + text_w + 10

            draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=(0, 0, 0))
            draw.text((x1 + 5, bg_y1 + 2), text, font=font, fill=color_rgb)

            img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        except Exception as e:
            print(f"PIL 绘制失败: {e}")
            text = f"{label} {int(conf * 100)}%"
            cv2.putText(img, text, (x1 + 5, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_bgr, 2)

        return img

    def draw_box(self, img, x1, y1, x2, y2, label, conf):
        color_bgr = (255, 238, 118)
        color_rgb = (118, 238, 255)
        line_len = min(int((x2 - x1) * 0.2), int((y2 - y1) * 0.2), 20)
        thickness = 2

        cv2.line(img, (x1, y1), (x1 + line_len, y1), color_bgr, thickness)
        cv2.line(img, (x1, y1), (x1, y1 + line_len), color_bgr, thickness)
        cv2.line(img, (x2, y1), (x2 - line_len, y1), color_bgr, thickness)
        cv2.line(img, (x2, y1), (x2, y1 + line_len), color_bgr, thickness)
        cv2.line(img, (x1, y2), (x1 + line_len, y2), color_bgr, thickness)
        cv2.line(img, (x1, y2), (x1, y2 - line_len), color_bgr, thickness)
        cv2.line(img, (x2, y2), (x2 - line_len, y2), color_bgr, thickness)
        cv2.line(img, (x2, y2), (x2, y2 - line_len), color_bgr, thickness)

        img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        font = self.get_chinese_font(20)

        conf_pct = int(conf * 100)
        text = f"{label} {conf_pct}%"

        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except Exception:
            text_w, text_h = 100, 20

        bg_y1 = max(0, y1 - text_h - 10)
        bg_y2 = y1
        bg_x1 = x1
        bg_x2 = x1 + text_w + 10

        draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=(0, 0, 0), outline=color_rgb)
        draw.text((x1 + 5, bg_y1 + 2), text, font=font, fill=color_rgb)

        img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        cv2.line(img, (cx - 5, cy), (cx + 5, cy), color_bgr, 1)
        cv2.line(img, (cx, cy - 5), (cx, cy + 5), color_bgr, 1)

        return img
