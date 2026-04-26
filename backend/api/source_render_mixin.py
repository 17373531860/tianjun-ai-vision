"""画面变换 + MediaPipe 叠加 + 检测框绘制 Mixin。

从 VideoSourceManager 中抽出 10 个相对独立的渲染方法。这些方法的特点：
  1. 主要操作 image ndarray，不触碰检测状态机
  2. 仅依赖少量 self 属性：video_rotation/video_flip_*/mediapipe_*
  3. 易测、易复用，是 mixin 拆分中的"低风险红利"

宿主类必须提供的实例属性：
  - self.video_rotation (int 0/90/180/270)
  - self.video_flip_h / self.video_flip_v (bool)
  - self.mediapipe_enabled / mediapipe_pose / mediapipe_hands / mediapipe_confidence
  - self._mp_pose / _mp_hands / _mp_draw / _mp_draw_styles / _mp_last_pose_results / _mp_last_hands_results
  - self._mp_frame_counter / _mp_process_interval

注意：保留 self._get_chinese_font 作为 wrapper（业务代码大量直接调它）。实现已迁到
source_geometry.get_chinese_font，自带 lru_cache。
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw

from backend.api.source_geometry import get_chinese_font as _ext_get_chinese_font


class RenderMixin:
    # ============== 画面变换：旋转 + 镜像 ==============
    def _apply_frame_transform(self, frame):
        """按通道配置对帧做旋转 + 镜像。

        顺序：先旋转（90° 倍数），再水平镜像，再垂直镜像。
        OpenCV 原生实现，零拷贝 90°/180°/270°，极低开销。
        """
        if frame is None:
            return frame
        rot = self.video_rotation
        if rot == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif rot == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif rot == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        if self.video_flip_h and self.video_flip_v:
            frame = cv2.flip(frame, -1)
        elif self.video_flip_h:
            frame = cv2.flip(frame, 1)
        elif self.video_flip_v:
            frame = cv2.flip(frame, 0)
        return frame

    def _has_display_transform(self) -> bool:
        """是否配置了任何画面变换（旋转/镜像）。无变换时走快路径跳过坐标映射。"""
        return bool(
            (self.video_rotation or 0) % 360 != 0
            or self.video_flip_h
            or self.video_flip_v
        )

    def _map_bbox_original_to_display(self, x: float, y: float, w: float, h: float):
        """把单个归一化 bbox 从原图坐标系映射到显示坐标系。

        变换顺序与 _apply_frame_transform 完全一致：先旋转, 再水平镜像, 再垂直镜像。
        坐标均为归一化值 (相对各自坐标系的宽高), 无需知道像素尺寸。
        """
        rot = (self.video_rotation or 0) % 360
        if rot == 90:
            # 顺时针 90°: 左上角 (x, y) -> (1 - y - h, x), 宽高交换
            nx, ny, nw, nh = 1.0 - y - h, x, h, w
        elif rot == 180:
            nx, ny, nw, nh = 1.0 - x - w, 1.0 - y - h, w, h
        elif rot == 270:
            # 逆时针 90°: 左上角 (x, y) -> (y, 1 - x - w), 宽高交换
            nx, ny, nw, nh = y, 1.0 - x - w, h, w
        else:
            nx, ny, nw, nh = x, y, w, h
        if self.video_flip_h:
            nx = 1.0 - nx - nw
        if self.video_flip_v:
            ny = 1.0 - ny - nh
        return nx, ny, nw, nh

    def _map_detections_original_to_display(self, detections):
        """就地把 detections 列表里每个 det 的 x/y/w/h 从原图坐标系映射到显示坐标系。

        无变换时直接返回, 零开销。归一化坐标下只做少量加减, 对上千目标也 < 1ms。
        """
        if not self._has_display_transform() or not detections:
            return detections
        for det in detections:
            if 'x' in det and 'y' in det and 'w' in det and 'h' in det:
                nx, ny, nw, nh = self._map_bbox_original_to_display(
                    float(det['x']), float(det['y']),
                    float(det['w']), float(det['h']),
                )
                det['x'], det['y'], det['w'], det['h'] = nx, ny, nw, nh
        return detections

    # ============== 字体 + 检测框绘制 ==============
    def _get_chinese_font(self, size=20):
        """获取中文字体（实际实现在 source_geometry.get_chinese_font，已加 lru_cache）"""
        return _ext_get_chinese_font(size)

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
            except Exception:
                text_w, text_h = 100, 20  # textbbox 老版本 PIL 不支持，回退默认值

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

    def _draw_box(self, img, x1, y1, x2, y2, label, conf):
        """绘制赛博朋克风格检测框（支持中文）"""
        color_bgr = (255, 238, 118)  # 冰蓝色 BGR
        color_rgb = (118, 238, 255)  # RGB for PIL
        line_len = min(int((x2 - x1) * 0.2), int((y2 - y1) * 0.2), 20)
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
        except Exception:
            text_w, text_h = 100, 20  # textbbox 老版本 PIL 不支持，回退默认值

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
