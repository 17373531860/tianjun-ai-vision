"""MediaPipeOverlay 组件 (v2.7.16 P7 第二刀, 组合优于继承)。

把 MediaPipe 姿态/手部识别相关功能从 VideoSourceManager 搬出, 形成独立组件.

字段所有权 (8 个内部状态):
  _mp_pose / _mp_hands             : lazy-loaded 模型句柄
  _mp_draw / _mp_draw_styles       : drawing utils
  _mp_last_pose_results            : 上一次推理结果 (帧间复用)
  _mp_last_hands_results
  _mp_frame_counter                : 跳帧计数
  _mp_process_interval             : 每 N 帧推理一次 (路由可设, 通过 __setattr__ 转发)

用户配置 (4 个 public 字段保留在 VSM, 路由 routes.py 直接赋值访问):
  mediapipe_enabled / mediapipe_pose / mediapipe_hands / mediapipe_confidence
  组件通过 self._host 反向读取这 4 个字段

公共 API (3 个方法):
  init()          : lazy-load 模型 (原 _init_mediapipe)
  release()       : 释放模型, 重置缓存 (原 _release_mediapipe)
  apply_overlay(frame): 在帧上画骨架 (原 _apply_mediapipe_overlay)
"""
import cv2


class MediaPipeOverlay:
    def __init__(self, host=None):
        self._host = host  # VSM 反向引用 (读取用户配置 mediapipe_enabled 等)
        self._mp_pose = None
        self._mp_hands = None
        self._mp_draw = None
        self._mp_draw_styles = None
        self._mp_last_pose_results = None
        self._mp_last_hands_results = None
        self._mp_frame_counter = 0
        self._mp_process_interval = 2  # 每隔 N 帧跑一次 MediaPipe (节省性能)

    def init(self):
        """Lazy-load MediaPipe models on first use."""
        host = self._host
        try:
            import mediapipe as mp
            self._mp_draw = mp.solutions.drawing_utils
            self._mp_draw_styles = mp.solutions.drawing_styles
            conf = max(0.1, min(1.0, host.mediapipe_confidence))
            if host.mediapipe_pose and self._mp_pose is None:
                self._mp_pose = mp.solutions.pose.Pose(
                    static_image_mode=False,
                    model_complexity=0,
                    min_detection_confidence=conf,
                    min_tracking_confidence=0.5,
                )
                print(f"[MediaPipe] Pose 模型已加载 (confidence={conf})")
            if host.mediapipe_hands and self._mp_hands is None:
                self._mp_hands = mp.solutions.hands.Hands(
                    static_image_mode=False,
                    max_num_hands=2,
                    model_complexity=0,
                    min_detection_confidence=conf,
                    min_tracking_confidence=0.5,
                )
                print("[MediaPipe] Hands 模型已加载")
        except ImportError:
            print("[MediaPipe] 警告: mediapipe 未安装，pip install mediapipe")
            host.mediapipe_enabled = False
        except Exception as e:
            print(f"[MediaPipe] 初始化失败: {e}")
            host.mediapipe_enabled = False

    def release(self):
        """Release MediaPipe resources."""
        if self._mp_pose is not None:
            try:
                self._mp_pose.close()
            except Exception:
                pass
            self._mp_pose = None
        if self._mp_hands is not None:
            try:
                self._mp_hands.close()
            except Exception:
                pass
            self._mp_hands = None
        self._mp_last_pose_results = None
        self._mp_last_hands_results = None
        self._mp_frame_counter = 0
        print("[MediaPipe] 资源已释放")

    def apply_overlay(self, frame):
        """Run MediaPipe on the frame (or reuse cached results) and draw landmarks."""
        host = self._host
        if not host.mediapipe_enabled:
            return frame

        if self._mp_draw is None:
            self.init()
            if not host.mediapipe_enabled:
                return frame

        import mediapipe as mp

        self._mp_frame_counter += 1
        should_process = (self._mp_frame_counter % max(self._mp_process_interval, 1)) == 0

        if should_process:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False

            if self._mp_pose is not None and host.mediapipe_pose:
                try:
                    self._mp_last_pose_results = self._mp_pose.process(rgb)
                except Exception:
                    self._mp_last_pose_results = None
            else:
                self._mp_last_pose_results = None

            if self._mp_hands is not None and host.mediapipe_hands:
                try:
                    self._mp_last_hands_results = self._mp_hands.process(rgb)
                except Exception:
                    self._mp_last_hands_results = None
            else:
                self._mp_last_hands_results = None

        if self._mp_last_pose_results and self._mp_last_pose_results.pose_landmarks:
            self._mp_draw.draw_landmarks(
                frame,
                self._mp_last_pose_results.pose_landmarks,
                mp.solutions.pose.POSE_CONNECTIONS,
                landmark_drawing_spec=self._mp_draw_styles.get_default_pose_landmarks_style(),
            )

        if self._mp_last_hands_results and self._mp_last_hands_results.multi_hand_landmarks:
            for hand_landmarks in self._mp_last_hands_results.multi_hand_landmarks:
                self._mp_draw.draw_landmarks(
                    frame,
                    hand_landmarks,
                    mp.solutions.hands.HAND_CONNECTIONS,
                    self._mp_draw_styles.get_default_hand_landmarks_style(),
                    self._mp_draw_styles.get_default_hand_connections_style(),
                )

        return frame
