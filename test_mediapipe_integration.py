#!/usr/bin/env python3
"""Test script for MediaPipe overlay integration.

Validates:
1. Backend: state variables, lazy-loading, overlay method, API, config persistence
2. Frontend: store settings, Settings UI, save/load sync
3. Frontend-backend field consistency
"""
import sys

PASS = 0
FAIL = 0


def check(desc, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {desc}")
    else:
        FAIL += 1
        print(f"  ✗ {desc}")


def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


# ===== Backend Tests =====
print("=" * 60)
print("Backend: backend/api/source.py")
print("=" * 60)

src = read_file('backend/api/source.py')

print("\n--- 1. State variables ---")
check("mediapipe_enabled flag", "self.mediapipe_enabled = False" in src)
check("mediapipe_pose flag", "self.mediapipe_pose = True" in src)
check("mediapipe_hands flag", "self.mediapipe_hands = True" in src)
check("_mp_pose lazy slot", "self._mp_pose = None" in src)
check("_mp_hands lazy slot", "self._mp_hands = None" in src)
check("_mp_draw lazy slot", "self._mp_draw = None" in src)
check("_mp_process_interval", "self._mp_process_interval = 2" in src)
check("_mp_last_pose_results cache", "self._mp_last_pose_results = None" in src)
check("_mp_last_hands_results cache", "self._mp_last_hands_results = None" in src)

print("\n--- 2. Lazy loading (_init_mediapipe) ---")
check("_init_mediapipe method exists", "def _init_mediapipe(self):" in src)
check("import mediapipe as mp", "import mediapipe as mp" in src)
check("Pose model created with model_complexity=0", "model_complexity=0" in src)
check("Hands model created", "mp.solutions.hands.Hands(" in src)
check("ImportError handling", "pip install mediapipe" in src)
check("Disables on import error", "self.mediapipe_enabled = False" in src)

print("\n--- 3. Release (_release_mediapipe) ---")
check("_release_mediapipe method exists", "def _release_mediapipe(self):" in src)
check("Pose close()", "self._mp_pose.close()" in src)
check("Hands close()", "self._mp_hands.close()" in src)
check("Resets cached results", "_mp_last_pose_results = None" in src)

print("\n--- 4. Overlay method (_apply_mediapipe_overlay) ---")
check("Method exists", "def _apply_mediapipe_overlay(self, frame):" in src)
check("Early return when disabled", "if not self.mediapipe_enabled:" in src)
check("Lazy init on first call", "self._init_mediapipe()" in src)
check("Frame counter for skip-frame", "self._mp_frame_counter += 1" in src)
check("BGR to RGB conversion", "cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)" in src)
check("Pose processing", "self._mp_pose.process(rgb)" in src)
check("Hands processing", "self._mp_hands.process(rgb)" in src)
check("Draw pose landmarks", "draw_landmarks" in src and "pose_landmarks" in src)
check("Draw hand landmarks", "HAND_CONNECTIONS" in src)

print("\n--- 5. Capture loop integration ---")
check("MediaPipe overlay before frame store", "self._apply_mediapipe_overlay(display_frame)" in src)
check("display_frame copy when enabled", "display_frame = original_frame.copy()" in src)
check("Recording uses original_frame (no overlay)", "_enqueue_frame_for_recording(original_frame)" in src)
check("Display frame stored to current_frame", "self.current_frame = display_frame" in src)

print("\n--- 6. Config persistence ---")
check("mediapipe_enabled in _load_device_config", "config.get('mediapipe_enabled', False)" in src)
check("mediapipe_pose in _load_device_config", "config.get('mediapipe_pose', True)" in src)
check("mediapipe_hands in _load_device_config", "config.get('mediapipe_hands', True)" in src)
check("mediapipe_interval in _load_device_config", "config.get('mediapipe_interval', 2)" in src)
check("mediapipe_enabled in _save_device_config", "'mediapipe_enabled': self.mediapipe_enabled" in src)

print("\n--- 7. StreamConfigRequest schema ---")
check("mediapipe_enabled field", "mediapipe_enabled: bool = False" in src)
check("mediapipe_pose field", "mediapipe_pose: bool = True" in src)
check("mediapipe_hands field", "mediapipe_hands: bool = True" in src)
check("mediapipe_interval field", "mediapipe_interval: int = 2" in src)

print("\n--- 8. API endpoints ---")
check("GET returns mediapipe_enabled", '"mediapipe_enabled": video_manager.mediapipe_enabled' in src)
check("GET returns mediapipe_pose", '"mediapipe_pose": video_manager.mediapipe_pose' in src)
check("POST sets mediapipe_enabled", "video_manager.mediapipe_enabled = req.mediapipe_enabled" in src)
check("POST releases on disable", "video_manager._release_mediapipe()" in src)

# ===== Frontend Store Tests =====
print("\n" + "=" * 60)
print("Frontend Store: frontend/src/store/useSystemStore.js")
print("=" * 60)

store_src = read_file('frontend/src/store/useSystemStore.js')

print("\n--- 9. Performance settings ---")
check("mediapipeEnabled default false", "mediapipeEnabled: false" in store_src)
check("mediapipePose default true", "mediapipePose: true" in store_src)
check("mediapipeHands default true", "mediapipeHands: true" in store_src)
check("mediapipeInterval default 2", "mediapipeInterval: 2" in store_src)

# ===== Frontend Settings Page Tests =====
print("\n" + "=" * 60)
print("Frontend Settings: frontend/src/views/Settings/index.vue")
print("=" * 60)

settings_src = read_file('frontend/src/views/Settings/index.vue')

print("\n--- 10. Settings UI ---")
check("MediaPipe card title", "MediaPipe 骨架叠加" in settings_src)
check("Aim icon imported", "Aim" in settings_src and "icons-vue" in settings_src)
check("mediapipeEnabled switch", 'store.performance.mediapipeEnabled' in settings_src)
check("mediapipePose switch", 'store.performance.mediapipePose' in settings_src)
check("mediapipeHands switch", 'store.performance.mediapipeHands' in settings_src)
check("mediapipeInterval input", 'store.performance.mediapipeInterval' in settings_src)
check("Disabled when mediapipe off", "!store.performance.mediapipeEnabled" in settings_src)

print("\n--- 11. Save to backend ---")
check("mediapipe_enabled sent to API", "mediapipe_enabled: store.performance.mediapipeEnabled" in settings_src)
check("mediapipe_pose sent to API", "mediapipe_pose: store.performance.mediapipePose" in settings_src)
check("mediapipe_hands sent to API", "mediapipe_hands: store.performance.mediapipeHands" in settings_src)
check("mediapipe_interval sent to API", "mediapipe_interval: store.performance.mediapipeInterval" in settings_src)

print("\n--- 12. Load from backend ---")
check("mediapipe_enabled loaded", "store.performance.mediapipeEnabled = res.data.mediapipe_enabled" in settings_src)
check("mediapipe_pose loaded", "store.performance.mediapipePose = res.data.mediapipe_pose" in settings_src)
check("mediapipe_hands loaded", "store.performance.mediapipeHands = res.data.mediapipe_hands" in settings_src)
check("mediapipe_interval loaded", "store.performance.mediapipeInterval = res.data.mediapipe_interval" in settings_src)

# ===== Requirements =====
print("\n" + "=" * 60)
print("Dependencies: backend/requirements.txt")
print("=" * 60)

req_src = read_file('backend/requirements.txt')
print("\n--- 13. Requirements ---")
check("mediapipe in requirements.txt", "mediapipe" in req_src)

# ===== Consistency =====
print("\n" + "=" * 60)
print("Frontend-Backend Consistency")
print("=" * 60)

print("\n--- 14. API field consistency ---")
fields = [
    ('mediapipe_enabled', src, settings_src),
    ('mediapipe_pose', src, settings_src),
    ('mediapipe_hands', src, settings_src),
    ('mediapipe_interval', src, settings_src),
]
for field, be, fe in fields:
    check(f"'{field}' in both backend and frontend", field in be and field in fe)

# ===== Summary =====
print("\n" + "=" * 60)
total = PASS + FAIL
print(f"Results: {PASS}/{total} passed, {FAIL} failed")
if FAIL > 0:
    print("SOME TESTS FAILED!")
    sys.exit(1)
else:
    print("ALL TESTS PASSED!")
    sys.exit(0)
