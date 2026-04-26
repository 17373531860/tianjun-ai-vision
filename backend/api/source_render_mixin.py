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
    # _apply_frame_transform / _has_display_transform / _map_bbox_original_to_display /
    # _map_detections_original_to_display 已迁至 source_video_transform.py (P7 第四刀)
    # 历史调用 self._apply_frame_transform(...) 等通过 VSM.__getattr__ 自动转发

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
