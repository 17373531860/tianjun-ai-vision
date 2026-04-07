#!/usr/bin/env python3
"""Debug script for tracking mode settlement logic."""
import sys, os, time, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from ultralytics import YOLO
import cv2

VIDEO = "6e9f8bfe45b862fd7ef33544b5baabe0.mp4"
MODEL = "best(gw1).pt"

ROI_POLYGON = [[0.888,0.7138],[0.8578,0.1807],[0.3214,0.1439],[0.3036,0.7414]]
EXPECTED_ITEMS = {"立柱": 3, "控制器": 1, "干燥剂": 1, "中间脚": 1}
CYCLE_STRATEGY = "roi_exit"
MAX_LOST_SEC = 5.0
GONE_CONFIRM_FRAMES = 5
GONE_THRESHOLD = 0
CONF_THRESHOLD = 0.50

def point_in_polygon(px, py, polygon):
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def is_in_roi(det):
    cx = det['x'] + det['w'] / 2
    cy = det['y'] + det['h'] / 2
    return point_in_polygon(cx, cy, ROI_POLYGON)

def bbox_iou(a, b):
    ax1,ay1,ax2,ay2 = a['x'],a['y'],a['x']+a['w'],a['y']+a['h']
    bx1,by1,bx2,by2 = b['x'],b['y'],b['x']+b['w'],b['y']+b['h']
    ix1,iy1 = max(ax1,bx1),max(ay1,by1)
    ix2,iy2 = min(ax2,bx2),min(ay2,by2)
    inter = max(0,ix2-ix1)*max(0,iy2-iy1)
    union = a['w']*a['h']+b['w']*b['h']-inter
    return inter/union if union>0 else 0

def bbox_center_dist(a, b):
    acx,acy = a['x']+a['w']/2, a['y']+a['h']/2
    bcx,bcy = b['x']+b['w']/2, b['y']+b['h']/2
    return ((acx-bcx)**2+(acy-bcy)**2)**0.5

def try_reid(new_bbox, cand_bbox):
    if bbox_iou(cand_bbox, new_bbox) > 0.2:
        return True
    dist = bbox_center_dist(cand_bbox, new_bbox)
    ref = max(cand_bbox['w'],cand_bbox['h'],new_bbox['w'],new_bbox['h'])
    if ref > 0 and dist < ref * 1.5:
        wr = max(cand_bbox['w'],new_bbox['w'])/max(min(cand_bbox['w'],new_bbox['w']),1e-6)
        hr = max(cand_bbox['h'],new_bbox['h'])/max(min(cand_bbox['h'],new_bbox['h']),1e-6)
        if wr < 3.0 and hr < 3.0:
            return True
    return False

def main():
    model = YOLO(MODEL)
    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    max_lost_frames = int(MAX_LOST_SEC * fps)
    
    print(f"Video: {VIDEO}, FPS={fps:.1f}, total_frames={total_frames}")
    print(f"max_lost_frames={max_lost_frames}, gone_confirm_frames={GONE_CONFIRM_FRAMES}")
    print(f"ROI polygon: {ROI_POLYGON}")
    print(f"Expected items: {EXPECTED_ITEMS}")
    print(f"Strategy: {CYCLE_STRATEGY}")
    print("="*80)
    
    tracking_objects = {}
    tracking_class_counters = {}
    tracking_lost_frames = {}
    tracking_display_map = {}
    tracking_recently_lost = {}
    tracking_transferred_ids = {}
    letter_map = {}
    letter_idx = 0
    order_seq = 0
    cycle_active = False
    cycle_start_time = None
    had_roi_objects = False
    prev_count = 0
    gone_frames = 0
    settled = False
    settle_count = 0
    
    frame_idx = 0
    max_frames = int(fps * 340)  # full video
    
    while cap.isOpened() and frame_idx < max_frames:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        
        h, w = frame.shape[:2]
        results = list(model.track(frame, conf=CONF_THRESHOLD, iou=0.5,
                                    imgsz=640, verbose=False, persist=True,
                                    tracker="bytetrack.yaml"))
        
        detections = []
        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i])
                label = model.names.get(cls_id, str(cls_id))
                conf_val = float(boxes.conf[i])
                x1,y1,x2,y2 = boxes.xyxy[i].tolist()
                tid = int(boxes.id[i]) if boxes.id is not None else -1
                detections.append({
                    'label': label, 'confidence': conf_val,
                    'x': x1/w, 'y': y1/h, 'w': (x2-x1)/w, 'h': (y2-y1)/h,
                    'track_id': tid
                })
        
        seen_track_ids = set()
        
        for det in detections:
            label = det['label']
            track_id = det['track_id']
            if not label or track_id < 0:
                continue
            
            new_bbox = {'x':det['x'],'y':det['y'],'w':det['w'],'h':det['h']}
            in_roi = is_in_roi(det)
            
            if track_id in tracking_transferred_ids:
                if time.time() - tracking_transferred_ids[track_id] < MAX_LOST_SEC:
                    continue
                else:
                    del tracking_transferred_ids[track_id]
            
            if track_id in tracking_objects:
                obj = tracking_objects[track_id]
                obj['bbox'] = new_bbox
                if in_roi or CYCLE_STRATEGY != 'roi_exit':
                    obj['last_seen'] = time.time()
                    tracking_lost_frames[track_id] = 0
                    seen_track_ids.add(track_id)
                continue
            
            if not in_roi:
                continue
            
            seen_track_ids.add(track_id)
            
            # Re-ID against recently_lost
            merged = False
            best_tid, best_obj, best_score = None, None, -1
            for ltid, lobj in list(tracking_recently_lost.items()):
                if lobj['class_name'] == label and try_reid(new_bbox, lobj['bbox']):
                    sc = bbox_iou(lobj['bbox'], new_bbox)
                    if sc > best_score:
                        best_score, best_tid, best_obj = sc, ltid, lobj
            if best_obj:
                tracking_objects[track_id] = {
                    'class_name': label, 'display_id': best_obj['display_id'],
                    'first_seen': time.time(), 'last_seen': time.time(),
                    'bbox': new_bbox, 'order_idx': best_obj['order_idx']
                }
                tracking_display_map[track_id] = best_obj['display_id']
                tracking_lost_frames[track_id] = 0
                del tracking_recently_lost[best_tid]
                merged = True
                if frame_idx % 50 == 0 or True:
                    print(f"  [F{frame_idx}] Re-ID: track {track_id} → {best_obj['display_id']}")
            
            if not merged:
                best_atid, best_aobj, best_as = None, None, -1
                for atid, aobj in list(tracking_objects.items()):
                    lf = tracking_lost_frames.get(atid, 0)
                    if lf > 0 and aobj['class_name'] == label and try_reid(new_bbox, aobj['bbox']):
                        sc = bbox_iou(aobj['bbox'], new_bbox)
                        if sc > best_as:
                            best_as, best_atid, best_aobj = sc, atid, aobj
                if best_aobj:
                    old_disp = best_aobj['display_id']
                    tracking_transferred_ids[best_atid] = time.time()
                    del tracking_objects[best_atid]
                    tracking_display_map.pop(best_atid, None)
                    tracking_lost_frames.pop(best_atid, None)
                    tracking_objects[track_id] = {
                        'class_name': label, 'display_id': old_disp,
                        'first_seen': best_aobj['first_seen'], 'last_seen': time.time(),
                        'bbox': new_bbox, 'order_idx': best_aobj['order_idx']
                    }
                    tracking_display_map[track_id] = old_disp
                    tracking_lost_frames[track_id] = 0
                    merged = True
                    print(f"  [F{frame_idx}] Re-ID (active): track {track_id} → {old_disp}")
            
            if not merged:
                if label not in letter_map:
                    letter_map[label] = chr(ord('A') + letter_idx % 26)
                    letter_idx += 1
                prefix = letter_map[label]
                current_count = tracking_class_counters.get(label, 0)
                expected_count = EXPECTED_ITEMS.get(label, 0)
                
                reuse_display = None
                if expected_count > 0 and current_count >= expected_count:
                    for lobj in tracking_recently_lost.values():
                        if lobj['class_name'] == label:
                            reuse_display = lobj['display_id']
                            break
                    if not reuse_display:
                        active_ids = {o['display_id'] for o in tracking_objects.values() if o['class_name'] == label}
                        for i in range(1, current_count + 1):
                            cand = f"{prefix}{i}"
                            if cand not in active_ids:
                                reuse_display = cand
                                break
                
                if expected_count > 0 and current_count >= expected_count and not reuse_display:
                    reuse_display = f"{prefix}{current_count}"
                
                if reuse_display:
                    display_id = reuse_display
                    print(f"  [F{frame_idx}] REUSE item: {display_id} (class={label}, track={track_id}, capped at {expected_count})")
                else:
                    current_count += 1
                    tracking_class_counters[label] = current_count
                    display_id = f"{prefix}{current_count}"
                    print(f"  [F{frame_idx}] NEW item: {display_id} (class={label}, track={track_id})")
                
                order_seq += 1
                tracking_objects[track_id] = {
                    'class_name': label, 'display_id': display_id,
                    'first_seen': time.time(), 'last_seen': time.time(),
                    'bbox': new_bbox, 'order_idx': order_seq
                }
                tracking_display_map[track_id] = display_id
                tracking_lost_frames[track_id] = 0
                
                if not cycle_active:
                    cycle_active = True
                    cycle_start_time = time.time()
                    print(f"  [F{frame_idx}] >>> CYCLE STARTED")
        
        # Increment lost frames
        for tid in list(tracking_lost_frames.keys()):
            if tid not in seen_track_ids and tid in tracking_objects:
                tracking_lost_frames[tid] = tracking_lost_frames.get(tid, 0) + 1
                if tracking_lost_frames[tid] >= max_lost_frames:
                    obj = tracking_objects[tid]
                    tracking_recently_lost[tid] = {
                        'class_name': obj['class_name'],
                        'display_id': obj['display_id'],
                        'bbox': obj['bbox'],
                        'lost_time': time.time(),
                        'order_idx': obj.get('order_idx', 0)
                    }
                    del tracking_objects[tid]
                    del tracking_lost_frames[tid]
                    tracking_display_map.pop(tid, None)
                    print(f"  [F{frame_idx}] EXPIRED: {obj['display_id']} (track={tid})")
        
        # Expire recently_lost
        cutoff = time.time() - MAX_LOST_SEC * 2
        for tid in list(tracking_recently_lost.keys()):
            if tracking_recently_lost[tid]['lost_time'] < cutoff:
                del tracking_recently_lost[tid]
        
        if not cycle_active:
            if frame_idx % 100 == 0:
                print(f"  [F{frame_idx}] No cycle active, {len(detections)} dets")
            continue
        
        if CYCLE_STRATEGY == 'roi_exit':
            active_count = len(seen_track_ids)
        else:
            active_count = sum(1 for tid in tracking_objects
                              if tracking_lost_frames.get(tid, 0) < max_lost_frames)
        
        if active_count > 0:
            had_roi_objects = True
        
        should_settle = False
        if CYCLE_STRATEGY == 'roi_exit':
            if had_roi_objects and active_count <= GONE_THRESHOLD:
                gone_frames += 1
                if gone_frames >= GONE_CONFIRM_FRAMES:
                    should_settle = True
            else:
                gone_frames = 0
        
        prev_count = active_count
        
        # Print status periodically or on changes
        n_in_roi = sum(1 for d in detections if is_in_roi(d) and d.get('track_id', -1) >= 0)
        n_total = sum(1 for d in detections if d.get('track_id', -1) >= 0)
        if frame_idx % 25 == 0 or should_settle or active_count == 0:
            print(f"  [F{frame_idx}] dets={n_total} in_roi={n_in_roi} "
                  f"active={active_count} gone_frames={gone_frames} "
                  f"objects={len(tracking_objects)} recently_lost={len(tracking_recently_lost)} "
                  f"counters={dict(tracking_class_counters)}")
        
        if should_settle:
            cycle_age = time.time() - (cycle_start_time or time.time())
            if cycle_age < MAX_LOST_SEC:
                should_settle = False
        
        if should_settle:
            settle_count += 1
            print(f"\n{'='*80}")
            print(f">>> SETTLEMENT #{settle_count} at frame {frame_idx}!")
            print(f"    Counters: {tracking_class_counters}")
            print(f"    Expected: {EXPECTED_ITEMS}")
            missing, extra = [], []
            for cls, exp in EXPECTED_ITEMS.items():
                actual = tracking_class_counters.get(cls, 0)
                if actual < exp: missing.append(f"{cls}: {actual}/{exp}")
                elif actual > exp: extra.append(f"{cls}: {actual}/{exp}")
            for cls, cnt in tracking_class_counters.items():
                if cls not in EXPECTED_ITEMS: extra.append(f"{cls}: {cnt}/0")
            
            if missing or extra:
                reasons = []
                if missing: reasons.append(f"缺件: {missing}")
                if extra: reasons.append(f"多件: {extra}")
                print(f"    Result: NG - {', '.join(reasons)}")
            else:
                print(f"    Result: OK - 装箱完整")
            print(f"{'='*80}\n")
            # Reset cycle
            tracking_objects.clear()
            tracking_class_counters.clear()
            tracking_display_map.clear()
            tracking_lost_frames.clear()
            tracking_recently_lost.clear()
            tracking_transferred_ids.clear()
            cycle_active = False
            cycle_start_time = None
            had_roi_objects = False
            prev_count = 0
            gone_frames = 0
            settled = True
    
    cap.release()
    
    if not settled:
        print(f"\n{'='*80}")
        print(f"NO SETTLEMENT after {frame_idx} frames!")
        print(f"Final state: active objects={len(tracking_objects)}, "
              f"recently_lost={len(tracking_recently_lost)}")
        print(f"Counters: {tracking_class_counters}")
        print(f"Object details:")
        for tid, obj in tracking_objects.items():
            lf = tracking_lost_frames.get(tid, 0)
            print(f"  track={tid} display={obj['display_id']} class={obj['class_name']} lost_frames={lf}")
        print(f"{'='*80}")

if __name__ == '__main__':
    main()
