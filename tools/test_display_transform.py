"""验证 "只翻显示, 不翻推理" 方案里的归一化坐标映射是否和图像变换一致。

思路:
1. 造一张带标记点的图
2. 用 cv2 做几何变换 (模拟 _apply_frame_transform)
3. 用 _map_bbox_original_to_display 算出标记点在变换后图上的期望位置
4. 真实变换后的图用 mask 定位标记点 → 和算出来的对比

运行:
    python tools/test_display_transform.py
"""
import sys
import os
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeMgr:
    """只模拟 VideoSourceManager 中坐标映射相关的几个属性/方法, 避免加载整个后端。"""
    def __init__(self, rot=0, flip_h=False, flip_v=False):
        self.video_rotation = rot
        self.video_flip_h = flip_h
        self.video_flip_v = flip_v

    def _has_display_transform(self) -> bool:
        return bool((self.video_rotation or 0) % 360 != 0 or self.video_flip_h or self.video_flip_v)

    def _apply_frame_transform(self, frame):
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

    def _map_bbox_original_to_display(self, x, y, w, h):
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


def _make_img_with_rect(W, H, rx, ry, rw, rh):
    """造一张 H*W 黑底图, 在 (rx..rx+rw, ry..ry+rh) 画一个白色矩形 (像素)。"""
    img = np.zeros((H, W), dtype=np.uint8)
    img[ry:ry + rh, rx:rx + rw] = 255
    return img


def _measured_bbox_norm(img):
    """在二值图上定位矩形, 返回归一化 (x, y, w, h) 相对图像宽高。"""
    ys, xs = np.where(img > 128)
    if len(xs) == 0:
        return None
    H, W = img.shape
    x1, x2 = xs.min(), xs.max()
    y1, y2 = ys.min(), ys.max()
    return (
        x1 / W,
        y1 / H,
        (x2 - x1 + 1) / W,
        (y2 - y1 + 1) / H,
    )


def _close(a, b, tol):
    return abs(a - b) <= tol


def run_one(rot, flip_h, flip_v, W=200, H=140, rx=30, ry=20, rw=40, rh=60):
    mgr = _FakeMgr(rot, flip_h, flip_v)
    img = _make_img_with_rect(W, H, rx, ry, rw, rh)

    # 原图归一化 bbox
    x = rx / W
    y = ry / H
    w = rw / W
    h = rh / H

    # 变换后图像里矩形的真实位置
    transformed = mgr._apply_frame_transform(img)
    measured = _measured_bbox_norm(transformed)

    # 用归一化坐标映射函数算出来的期望位置
    mapped = mgr._map_bbox_original_to_display(x, y, w, h)

    # 1 像素容差: 1 / min(W, H)
    tol = 1.0 / min(W, H)
    ok = all(_close(a, b, tol) for a, b in zip(mapped, measured))
    tag = f"rot={rot} flip_h={flip_h} flip_v={flip_v}"
    if ok:
        print(f"[PASS] {tag}")
    else:
        print(f"[FAIL] {tag}")
        print(f"       expect (from image) = {measured}")
        print(f"       got    (from map)   = {mapped}")
    return ok


def main():
    cases = []
    for rot in (0, 90, 180, 270):
        for fh in (False, True):
            for fv in (False, True):
                cases.append((rot, fh, fv))

    failures = 0
    for rot, fh, fv in cases:
        if not run_one(rot, fh, fv):
            failures += 1

    print("----")
    if failures == 0:
        print(f"OK: {len(cases)} cases all passed.")
    else:
        print(f"FAILED: {failures}/{len(cases)} cases failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
