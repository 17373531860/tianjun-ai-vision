"""
lightguide — 投影光引导子系统 (P1)  /api/v1/lightguide/*

把「投影仪投到工作台的引导画布」接到现有检测主线上。本模块只做两件事：

1. 标定：投影窗全屏显示本模块生成的 ArUco 标记阵 (pattern 端点)，后端从工位
   帧缓存自动识别标记、与已知投影坐标配对，findHomography 求 相机→投影 映射
   (solve 端点)。全自动零点击，对比手动四点法：带畸变鲁棒 RANSAC + 重投影误差
   可观测 + 秒级重标。
2. 标定结果按工位持久化到 SystemConfig KV (key='lightguide.channel.{n}')，
   不加新表、无迁移。引导内容本身不经过本模块——投影窗前端直接轮询
   /source/detection/results (步骤/检测框/ROI 全在里面)，用标定矩阵在前端变换。

设计约束：
- 完全不碰检测热路径。取帧走 channel_manager.channels[ch].get_frame() 帧缓存
  只读路径 (与触发中心 pixel_region 同源)。
- cv2 延迟到函数体内 import (AGENTS.md 不变量 #10：不得早于
  OPENCV_FFMPEG_CAPTURE_OPTIONS 设置执行)。
"""
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.models import SystemConfig

router = APIRouter()

_KV_PREFIX = "lightguide.channel."
_PARAMS_KEY = "lightguide.params"

# ==================== 引导参数 (P3: 设置页可调, 全局一份) ====================
#
# 每项: (默认值, 下限, 上限)。None 界限表示 bool。
# 前端投影窗每次进入引导态拉取; 不存 per-channel —— 悬停时长/漂移阈值是
# 人机工学参数, 与具体投影仪无关 (标定才是 per-channel 的)。
_PARAM_SPEC: Dict[str, tuple] = {
    "drift_interval_s":   (20, 5, 600),      # 漂移自检周期
    "drift_threshold_px": (6.0, 2.0, 50.0),  # 平均漂移超过即自动重标
    "auto_recalibrate":   (True, None, None),  # 漂移后是否自动重标 (关=只提示)
    "hover_dwell_ms":     (1200, 400, 5000),  # 悬停确认累计时长
    "hover_delta":        (14.0, 5.0, 60.0),  # 亮度偏离基线阈值
    "hover_poll_ms":      (250, 100, 1000),   # 悬停采样周期
    "calib_settle_ms":    (1500, 300, 5000),  # 标定图案投出后等待曝光稳定
    "pattern_cols":       (4, 2, 8),          # 标定阵列数
    "pattern_rows":       (3, 2, 6),          # 标定阵行数
    "brightness":         (1.0, 0.3, 1.0),    # 引导画面亮度系数 (锚点/确认按钮不受影响)
    "flow_path":          (True, None, None), # 流向引导路径动效
}


def _normalize_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    """按 spec 归一化: 未知键丢弃、越界钳制、类型不合退默认。"""
    out: Dict[str, Any] = {}
    for key, (default, lo, hi) in _PARAM_SPEC.items():
        value = raw.get(key, default)
        if isinstance(default, bool):
            out[key] = bool(value) if isinstance(value, bool) else default
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            num = float(default)
        num = max(float(lo), min(float(hi), num))
        out[key] = int(round(num)) if isinstance(default, int) else round(num, 2)
    return out


def _load_params(db: Session) -> Dict[str, Any]:
    row = db.query(SystemConfig).filter(
        SystemConfig.key == _PARAMS_KEY).first()
    stored: Dict[str, Any] = {}
    if row and row.value:
        try:
            data = json.loads(row.value)
            if isinstance(data, dict):
                stored = data
        except Exception:
            stored = {}
    return _normalize_params(stored)

# ArUco 字典固定 4X4_50：标记少、格子粗，投影→相机二次成像下最抗糊。
# id 分配契约：0~43 给标定图案阵，46~49 给引导画面四角自愈锚点，永不混用。
_ARUCO_DICT_NAME = "DICT_4X4_50"
_ANCHOR_IDS = [46, 47, 48, 49]   # TL, TR, BR, BL
_ANCHOR_PAD_RATIO = 0.18         # 白底 tile 内标记留白比例 (黑底上必须有白圈才可检)


# ==================== KV 存取 ====================

def _kv_key(channel: int) -> str:
    return f"{_KV_PREFIX}{int(channel)}"


def _load_calibration(db: Session, channel: int) -> Optional[Dict[str, Any]]:
    row = db.query(SystemConfig).filter(
        SystemConfig.key == _kv_key(channel)).first()
    if not row or not row.value:
        return None
    try:
        data = json.loads(row.value)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _save_calibration(db: Session, channel: int, data: Dict[str, Any]) -> None:
    key = _kv_key(channel)
    value = json.dumps(data, ensure_ascii=False)
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value,
                            description=f"投影光引导标定 (工位 {channel})"))
    db.commit()


# ==================== 标定图案几何 (前后端共同契约) ====================
#
# 布局是 (proj_w, proj_h, cols, rows) 的纯函数：pattern 端点按它画图，
# solve 端点按它反查每个标记 id 的投影坐标。两边永远一致，不需要传点表。

def _pattern_layout(proj_w: int, proj_h: int, cols: int, rows: int) -> List[Dict[str, Any]]:
    """返回每个标记的 {id, corners}，corners 为投影像素坐标 (tl,tr,br,bl)。"""
    margin = int(round(min(proj_w, proj_h) * 0.06))
    cell_w = (proj_w - 2 * margin) / cols
    cell_h = (proj_h - 2 * margin) / rows
    side = int(round(min(cell_w, cell_h) * 0.52))
    markers = []
    for r in range(rows):
        for c in range(cols):
            marker_id = r * cols + c
            cx = margin + cell_w * (c + 0.5)
            cy = margin + cell_h * (r + 0.5)
            x0, y0 = cx - side / 2.0, cy - side / 2.0
            markers.append({
                "id": marker_id,
                "corners": [
                    [x0, y0], [x0 + side, y0],
                    [x0 + side, y0 + side], [x0, y0 + side],
                ],
            })
    return markers


def _validate_pattern_params(proj_w: int, proj_h: int, cols: int, rows: int) -> None:
    if not (320 <= proj_w <= 8192 and 240 <= proj_h <= 8192):
        raise HTTPException(status_code=400, detail="投影分辨率超出合理范围 (320~8192)")
    if not (2 <= cols <= 8 and 2 <= rows <= 6):
        raise HTTPException(status_code=400, detail="标记阵规格超出范围 (cols 2~8, rows 2~6)")
    if cols * rows > 44:
        raise HTTPException(status_code=400, detail="标定阵最多 44 个标记 (id 46~49 保留给自愈锚点)")


# ==================== 自愈锚点几何 (前后端共同契约) ====================
#
# 引导画面四角常驻 4 个小 ArUco (白底 tile), 运行中 verify 端点随时抽帧比对
# "锚点实测位置 vs 标定矩阵推算位置", 漂移超阈值由前端自动触发重标——
# 投影仪被撞歪/工作台挪动无需人工发现。

def _anchor_layout(proj_w: int, proj_h: int) -> List[Dict[str, Any]]:
    """返回四角锚点 tile 与内部标记角点。

    每项: {id, tile: [x, y, size], corners: [[x,y]×4]}
    tile 是前端绘制白底方块的几何; corners 是标记本体四角 (verify 的期望位置)。
    """
    size = max(40, int(round(min(proj_w, proj_h) * 0.055)))
    edge = 10
    positions = [
        (edge, edge),                                   # TL
        (proj_w - edge - size, edge),                   # TR
        (proj_w - edge - size, proj_h - edge - size),   # BR
        (edge, proj_h - edge - size),                   # BL
    ]
    pad = int(round(size * _ANCHOR_PAD_RATIO))
    inner = size - 2 * pad
    out = []
    for marker_id, (x, y) in zip(_ANCHOR_IDS, positions):
        ix, iy = x + pad, y + pad
        out.append({
            "id": marker_id,
            "tile": [x, y, size],
            "corners": [
                [ix, iy], [ix + inner, iy],
                [ix + inner, iy + inner], [ix, iy + inner],
            ],
        })
    return out


# ==================== 取帧 (只读帧缓存, 与 pixel_region 同源) ====================

def _grab_frame(channel: int):
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.channels.get(channel)
    if mgr is None:
        return None
    return mgr.get_frame()


# ==================== 端点 ====================

class ParamsUpdate(BaseModel):
    """引导参数更新体。全部可选, 只更新给出的键; 越界值钳制、类型不合退默认。"""
    drift_interval_s: Optional[float] = Field(None, description="漂移自检周期 (秒, 5~600)")
    drift_threshold_px: Optional[float] = Field(None, description="平均漂移阈值 (px, 2~50)")
    auto_recalibrate: Optional[bool] = Field(None, description="漂移后自动重标 (关=仅提示)")
    hover_dwell_ms: Optional[float] = Field(None, description="悬停确认时长 (ms, 400~5000)")
    hover_delta: Optional[float] = Field(None, description="悬停亮度偏移阈值 (5~60)")
    hover_poll_ms: Optional[float] = Field(None, description="悬停采样周期 (ms, 100~1000)")
    calib_settle_ms: Optional[float] = Field(None, description="标定曝光稳定等待 (ms, 300~5000)")
    pattern_cols: Optional[float] = Field(None, description="标定阵列数 (2~8)")
    pattern_rows: Optional[float] = Field(None, description="标定阵行数 (2~6)")
    brightness: Optional[float] = Field(None, description="引导亮度系数 (0.3~1.0)")
    flow_path: Optional[bool] = Field(None, description="流向引导路径动效")


@router.get("/params")
def get_params(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """引导参数 (全局一份, 缺省即出厂默认)。投影窗进入引导态时拉取。"""
    return {"params": _load_params(db)}


@router.put("/params",
            dependencies=[Depends(require_perm("settings.edit"))])
def set_params(payload: ParamsUpdate, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """更新引导参数 (只改给出的键), 返回归一化后的完整参数。"""
    current = _load_params(db)
    updates = payload.model_dump(exclude_none=True)
    current.update(updates)
    normalized = _normalize_params(current)
    value = json.dumps(normalized, ensure_ascii=False)
    row = db.query(SystemConfig).filter(
        SystemConfig.key == _PARAMS_KEY).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=_PARAMS_KEY, value=value,
                            description="投影光引导参数 (全局)"))
    db.commit()
    return {"status": "success", "params": normalized}


@router.get("/config")
def get_config(channel: int = Query(0), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """工位标定状态 + 当前帧可用性。投影窗启动时调用。"""
    calib = _load_calibration(db, channel)
    frame = _grab_frame(channel)
    frame_info: Dict[str, Any] = {"available": frame is not None}
    if frame is not None:
        h, w = frame.shape[:2]
        frame_info.update({"width": int(w), "height": int(h)})
    return {
        "channel": channel,
        "calibrated": calib is not None,
        "calibration": calib,
        "frame": frame_info,
        "aruco_dict": _ARUCO_DICT_NAME,
    }


@router.delete("/config",
               dependencies=[Depends(require_perm("settings.edit"))])
def delete_config(channel: int = Query(0), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """清除工位标定 (重标前/换投影仪时用)。"""
    row = db.query(SystemConfig).filter(
        SystemConfig.key == _kv_key(channel)).first()
    if row:
        db.delete(row)
        db.commit()
    return {"channel": channel, "calibrated": False}


@router.get("/calibration/pattern")
def get_calibration_pattern(
    proj_w: int = Query(1920, description="投影窗宽 (px)"),
    proj_h: int = Query(1080, description="投影窗高 (px)"),
    cols: int = Query(4),
    rows: int = Query(3),
) -> Response:
    """生成 ArUco 标记阵 PNG。投影窗全屏 1:1 显示 (不得缩放/加黑边)。"""
    _validate_pattern_params(proj_w, proj_h, cols, rows)
    import cv2
    import numpy as np

    canvas = np.full((proj_h, proj_w), 255, dtype=np.uint8)
    aruco_dict = cv2.aruco.getPredefinedDictionary(
        getattr(cv2.aruco, _ARUCO_DICT_NAME))
    for m in _pattern_layout(proj_w, proj_h, cols, rows):
        (x0, y0), (x1, _), (_, y2), _ = m["corners"]
        side = int(round(x1 - x0))
        if side < 16:
            raise HTTPException(status_code=400, detail="标记尺寸过小, 请减少 cols/rows")
        img = cv2.aruco.generateImageMarker(aruco_dict, m["id"], side)
        yy, xx = int(round(y0)), int(round(x0))
        canvas[yy:yy + side, xx:xx + side] = img
    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        raise HTTPException(status_code=500, detail="PNG 编码失败")
    return Response(content=buf.tobytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


class SolveRequest(BaseModel):
    channel: int = 0
    proj_w: int = Field(1920, description="投影窗实际宽 (与 pattern 请求一致)")
    proj_h: int = Field(1080, description="投影窗实际高 (与 pattern 请求一致)")
    cols: int = 4
    rows: int = 3
    grab_frames: int = Field(5, ge=1, le=15, description="采样帧数 (多帧平均抗噪)")


@router.post("/calibration/solve",
             dependencies=[Depends(require_perm("settings.edit"))])
def solve_calibration(payload: SolveRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """从工位帧缓存自动识别标记阵并求解 相机→投影 homography。

    前置条件：投影窗正全屏显示 pattern 端点的图案且已稳定 (前端等曝光后再调)。
    多帧采样对每个标记的角点取平均，RANSAC 求解，返回重投影误差。
    """
    _validate_pattern_params(payload.proj_w, payload.proj_h, payload.cols, payload.rows)
    import cv2
    import numpy as np

    aruco_dict = cv2.aruco.getPredefinedDictionary(
        getattr(cv2.aruco, _ARUCO_DICT_NAME))
    detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())

    expected = {m["id"]: m["corners"]
                for m in _pattern_layout(payload.proj_w, payload.proj_h,
                                         payload.cols, payload.rows)}

    # 多帧采样：每个标记 id 的 4 角点在相机坐标下累计取均值
    acc: Dict[int, List[Any]] = {}
    cam_w = cam_h = None
    frames_used = 0
    for i in range(payload.grab_frames):
        frame = _grab_frame(payload.channel)
        if frame is None:
            if frames_used == 0 and i == payload.grab_frames - 1:
                raise HTTPException(
                    status_code=400,
                    detail=f"工位 {payload.channel} 当前无画面, 无法标定 (请先启动视频源)")
            time.sleep(0.12)
            continue
        cam_h, cam_w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        corners, ids, _rejected = detector.detectMarkers(gray)
        if ids is not None:
            for marker_corners, marker_id in zip(corners, ids.flatten()):
                mid = int(marker_id)
                if mid in expected:
                    acc.setdefault(mid, []).append(
                        marker_corners.reshape(4, 2).astype(np.float64))
        frames_used += 1
        if i < payload.grab_frames - 1:
            time.sleep(0.12)

    if frames_used == 0:
        raise HTTPException(
            status_code=400,
            detail=f"工位 {payload.channel} 当前无画面, 无法标定 (请先启动视频源)")

    total = len(expected)
    matched_ids = sorted(acc.keys())
    if len(matched_ids) < 4:
        raise HTTPException(
            status_code=422,
            detail=(f"仅识别到 {len(matched_ids)}/{total} 个标记 (至少需 4 个)。"
                    "请确认投影图案完整落在相机视野内、对焦清晰、环境光不过曝"))

    cam_pts, proj_pts = [], []
    for mid in matched_ids:
        mean_corners = np.mean(np.stack(acc[mid]), axis=0)  # (4,2)
        for k in range(4):
            cam_pts.append(mean_corners[k])
            proj_pts.append(expected[mid][k])
    cam_arr = np.asarray(cam_pts, dtype=np.float64)
    proj_arr = np.asarray(proj_pts, dtype=np.float64)

    H, inlier_mask = cv2.findHomography(cam_arr, proj_arr, cv2.RANSAC, 5.0)
    if H is None:
        raise HTTPException(status_code=422, detail="homography 求解失败 (角点共线或噪声过大)")

    # 重投影误差 (仅 inlier)
    projected = cv2.perspectiveTransform(
        cam_arr.reshape(-1, 1, 2), H).reshape(-1, 2)
    err = np.linalg.norm(projected - proj_arr, axis=1)
    inliers = inlier_mask.flatten().astype(bool) if inlier_mask is not None \
        else np.ones(len(err), dtype=bool)
    inlier_err = err[inliers] if inliers.any() else err

    calib = {
        "homography": H.tolist(),          # 3x3, 相机像素 → 投影像素 (下同)
        "cam_width": int(cam_w),
        "cam_height": int(cam_h),
        "proj_width": payload.proj_w,
        "proj_height": payload.proj_h,
        "pattern": {"cols": payload.cols, "rows": payload.rows,
                    "dict": _ARUCO_DICT_NAME},
        "markers_matched": len(matched_ids),
        "markers_total": total,
        "frames_used": frames_used,
        "reproj_error_px": round(float(np.mean(inlier_err)), 2),
        "reproj_error_max_px": round(float(np.max(inlier_err)), 2),
        "inlier_ratio": round(float(inliers.mean()), 3),
        "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_calibration(db, payload.channel, calib)
    return {"status": "success", "channel": payload.channel, "calibration": calib}


# ==================== P2: 自愈锚点端点 ====================

@router.get("/calibration/anchors")
def get_calibration_anchors(
    proj_w: int = Query(1920), proj_h: int = Query(1080),
) -> Dict[str, Any]:
    """四角自愈锚点布局 (前端据此在引导画布上绘制锚点 tile)。"""
    if not (320 <= proj_w <= 8192 and 240 <= proj_h <= 8192):
        raise HTTPException(status_code=400, detail="投影分辨率超出合理范围 (320~8192)")
    return {"markers": _anchor_layout(proj_w, proj_h),
            "aruco_dict": _ARUCO_DICT_NAME}


@router.get("/calibration/marker")
def get_calibration_marker(
    marker_id: int = Query(..., ge=0, le=49),
    size: int = Query(64, ge=32, le=512, description="白底 tile 边长 (px)"),
) -> Response:
    """单个 ArUco 标记 PNG (白底 tile + 内缩标记, 供前端画锚点)。"""
    import cv2
    import numpy as np

    pad = int(round(size * _ANCHOR_PAD_RATIO))
    inner = size - 2 * pad
    tile = np.full((size, size), 255, dtype=np.uint8)
    aruco_dict = cv2.aruco.getPredefinedDictionary(
        getattr(cv2.aruco, _ARUCO_DICT_NAME))
    tile[pad:pad + inner, pad:pad + inner] = \
        cv2.aruco.generateImageMarker(aruco_dict, marker_id, inner)
    ok, buf = cv2.imencode(".png", tile)
    if not ok:
        raise HTTPException(status_code=500, detail="PNG 编码失败")
    return Response(content=buf.tobytes(), media_type="image/png",
                    headers={"Cache-Control": "max-age=86400"})


class VerifyRequest(BaseModel):
    channel: int = 0
    grab_frames: int = Field(2, ge=1, le=8)


@router.post("/calibration/verify")
def verify_calibration(payload: VerifyRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """自愈校验: 抽帧找四角锚点, 用存量 homography 推算其投影位置, 与期望比对。

    返回漂移量, 阈值判断留给前端 (锚点被手/工件遮挡时 found 少, 前端只在
    found>=3 时才信任 drift, 防误触发重标)。
    """
    calib = _load_calibration(db, payload.channel)
    if not calib:
        raise HTTPException(status_code=409, detail=f"工位 {payload.channel} 尚未标定, 无法校验")
    import cv2
    import numpy as np

    H = np.asarray(calib["homography"], dtype=np.float64)
    expected = {m["id"]: np.asarray(m["corners"], dtype=np.float64)
                for m in _anchor_layout(calib["proj_width"], calib["proj_height"])}

    aruco_dict = cv2.aruco.getPredefinedDictionary(
        getattr(cv2.aruco, _ARUCO_DICT_NAME))
    detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())

    acc: Dict[int, List[Any]] = {}
    frames_used = 0
    for i in range(payload.grab_frames):
        frame = _grab_frame(payload.channel)
        if frame is None:
            time.sleep(0.08)
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        corners, ids, _ = detector.detectMarkers(gray)
        if ids is not None:
            for marker_corners, marker_id in zip(corners, ids.flatten()):
                mid = int(marker_id)
                if mid in expected:
                    acc.setdefault(mid, []).append(
                        marker_corners.reshape(4, 2).astype(np.float64))
        frames_used += 1
        if i < payload.grab_frames - 1:
            time.sleep(0.08)

    if frames_used == 0:
        raise HTTPException(status_code=400,
                            detail=f"工位 {payload.channel} 当前无画面, 无法校验")

    drifts = []
    for mid, samples in acc.items():
        cam_corners = np.mean(np.stack(samples), axis=0)          # (4,2)
        proj_pts = cv2.perspectiveTransform(
            cam_corners.reshape(-1, 1, 2), H).reshape(-1, 2)
        drifts.extend(np.linalg.norm(proj_pts - expected[mid], axis=1).tolist())

    found = len(acc)
    return {
        "channel": payload.channel,
        "anchors_expected": len(expected),
        "anchors_found": found,
        "frames_used": frames_used,
        "drift_px": round(float(np.mean(drifts)), 2) if drifts else None,
        "drift_max_px": round(float(np.max(drifts)), 2) if drifts else None,
    }


# ==================== P2: 投影按钮悬停采样 ====================

class SampleRequest(BaseModel):
    channel: int = 0
    polygon: List[List[float]] = Field(
        ..., description="相机归一化多边形 (>=3 点), 前端用 H⁻¹ 从投影空间反算")


@router.post("/interaction/sample")
def interaction_sample(payload: SampleRequest) -> Dict[str, Any]:
    """采样相机帧上指定多边形区域的平均亮度 (投影按钮悬停判定的传感原语)。

    悬停判定逻辑全在前端: 按钮亮起时采一次基线, 之后轮询比对亮度差 —
    手伸进按钮投影区反射率变化 → 亮度偏移。后端只读帧不留状态。
    """
    poly = payload.polygon
    if len(poly) < 3 or any(
            len(p) != 2 or not (0.0 <= p[0] <= 1.0 and 0.0 <= p[1] <= 1.0)
            for p in poly):
        raise HTTPException(status_code=400, detail="polygon 须为 >=3 个归一化 [x,y] 点")
    frame = _grab_frame(payload.channel)
    if frame is None:
        raise HTTPException(status_code=400,
                            detail=f"工位 {payload.channel} 当前无画面")
    import cv2
    import numpy as np

    h, w = frame.shape[:2]
    pts = np.asarray([[int(round(p[0] * w)), int(round(p[1] * h))] for p in poly],
                     dtype=np.int32)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    if int(mask.sum()) == 0:
        raise HTTPException(status_code=400, detail="polygon 在画面内无有效像素")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    mean_gray = float(cv2.mean(gray, mask=mask)[0])
    mean_bgr = [round(float(v), 2) for v in cv2.mean(frame, mask=mask)[:3]] \
        if frame.ndim == 3 else [round(mean_gray, 2)] * 3
    return {
        "channel": payload.channel,
        "mean_gray": round(mean_gray, 2),
        "mean_bgr": mean_bgr,
        "frame_size": [int(w), int(h)],
        "ts": time.time(),
    }
