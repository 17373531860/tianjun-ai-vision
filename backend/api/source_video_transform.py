"""VideoTransform 组件 (v2.7.16 P7 第四刀, 组合优于继承)。

把"画面旋转 + 镜像 + 坐标映射"子领域从 VSM/RenderMixin 抽出为独立组件:
  - 自持 3 个状态字段 (rotation / flip_h / flip_v)
  - 4 个公共方法: 帧变换 + 是否有变换 + bbox 映射 + 整批 dets 映射
  - 无 host 反向依赖 (纯函数式), 易于单测

数据所有权:
  - 旧: VSM.video_rotation/flip_h/flip_v (3 字段) + 4 方法重复定义在 source.py 和 source_render_mixin.py
  - 新: VideoTransform 自持, 通过 VSM.__getattr__/__setattr__ 透明转发

公共 API:
  apply_to_frame(frame)               : 执行旋转 + 镜像
  has_transform() -> bool             : 是否配置了任何变换
  map_bbox_to_display(x,y,w,h)        : 单个 bbox 坐标映射 (原图 → 显示)
  map_detections_to_display(dets)     : 整批 dets 列表就地映射

历史方法名兼容 (通过 VSM.__getattr__ 转发, 兼容前缀 _apply/_has/_map):
  _apply_frame_transform              → apply_to_frame
  _has_display_transform              → has_transform
  _map_bbox_original_to_display       → map_bbox_to_display
  _map_detections_original_to_display → map_detections_to_display
"""
import cv2


class VideoTransform:
    def __init__(self):
        self.video_rotation = 0      # 0 / 90 / 180 / 270
        self.video_flip_h = False    # 左右镜像
        self.video_flip_v = False    # 上下镜像

    def apply_to_frame(self, frame):
        """按通道配置对帧做旋转 + 镜像.

        顺序: 先旋转 (90° 倍数), 再水平镜像, 再垂直镜像.
        OpenCV 原生实现, 零拷贝 90°/180°/270°, 极低开销.
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

    def has_transform(self) -> bool:
        """是否配置了任何画面变换 (旋转/镜像). 无变换时走快路径跳过坐标映射."""
        return bool(
            (self.video_rotation or 0) % 360 != 0
            or self.video_flip_h
            or self.video_flip_v
        )

    def map_bbox_to_display(self, x: float, y: float, w: float, h: float):
        """把单个归一化 bbox 从原图坐标系映射到显示坐标系.

        变换顺序与 apply_to_frame 完全一致: 先旋转, 再水平镜像, 再垂直镜像.
        坐标均为归一化值 (相对各自坐标系的宽高), 无需知道像素尺寸.
        """
        rot = (self.video_rotation or 0) % 360
        if rot == 90:
            nx, ny, nw, nh = 1.0 - y - h, x, h, w
        elif rot == 180:
            nx, ny, nw, nh = 1.0 - x - w, 1.0 - y - h, w, h
        elif rot == 270:
            nx, ny, nw, nh = y, 1.0 - x - w, h, w
        else:
            nx, ny, nw, nh = x, y, w, h
        if self.video_flip_h:
            nx = 1.0 - nx - nw
        if self.video_flip_v:
            ny = 1.0 - ny - nh
        return nx, ny, nw, nh

    def map_detections_to_display(self, detections):
        """就地把 detections 列表里每个 det 的 x/y/w/h 从原图坐标系映射到显示坐标系.

        无变换时直接返回, 零开销. 归一化坐标下只做少量加减, 对上千目标也 < 1ms.
        """
        if not self.has_transform() or not detections:
            return detections
        for det in detections:
            if 'x' in det and 'y' in det and 'w' in det and 'h' in det:
                nx, ny, nw, nh = self.map_bbox_to_display(
                    float(det['x']), float(det['y']),
                    float(det['w']), float(det['h']),
                )
                det['x'], det['y'], det['w'], det['h'] = nx, ny, nw, nh
        return detections
