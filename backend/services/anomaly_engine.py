# -*- coding: utf-8 -*-
"""异常检测引擎 (2026-09 全量批次) — 只学合格品的第二条质检路径。

定位: 很多质检几乎没有缺陷样本, 检测模型无从学起 (调研点 1-5)。本引擎按
PatchCore / SuperADD 思路自实现 (无 anomalib 重依赖, 复用已有 torch):

  建库 (enroll): N 张合格品图 → 骨干多层 patch 特征 (每层独立记忆库,
                 SuperADD 风格: 浅层管纹理/深层管结构, 分层比拼接更稳)
                 → 每层随机子采样 → npz 落盘;
  评分 (score) : 测试图逐层抽特征 → 每层 patch 对该层记忆库的最近邻距离
                 → 层分数 = 最大 patch 距离 → 图像分数 = 各层分数均值;
  阈值         : 建库时留一法自评分分布 (均值 + 3σ) 给出建议阈值, 可调。

骨干 (2026-09 升级, 可插拔):
  - dinov2_vits14 (默认优先): Meta DINOv2 ViT-S/14, 代码与权重均 Apache 2.0
    (已核查; DINOv3 是定制许可要求 "Built with DINOv3" 标识, 暂不采用)。
    自监督十亿级图预训练, 免微调即得细粒度密集特征, 对纹理/结构异常远强于
    ImageNet 分类骨干。经 torch.hub 加载, 离线工控机预置 hub 缓存即可
    (安装包 build 侧落位 ~/.cache/torch/hub, 见 build-release skill)。
  - resnet18 (兜底): torchvision, 权重不可得时随机初始化仍可分辨明显缺陷。
  选择顺序: TJ_ANOMALY_BACKBONE 显式指定 > dinov2 可用 > resnet18。
  评分永远用建库时记录的骨干 (meta.backbone), 跨骨干不混算。

持久化: {TIANJUN_DATA_DIR}/anomaly_banks/{bank_id}/ 下 bank.npz + meta.json。
  格式 v2: npz 内 bank_0..bank_N 每层一库; v1 老库 (单 bank 键) 仍可评分
  (走 resnet18 拼接老路径), 升级零迁移。
线程安全: 建库/评分共用推理锁 (低频操作串行足够); tmp+rename 原子落位。
测试可用 TJ_ANOMALY_PRETRAINED=0 强制 resnet18 随机权重 (无网确定性)。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Optional

import numpy as np

_IMG_SIZE = 224
_MAX_BANK_VECTORS = 10000       # 每层上限
# RLock: create_bank/score_image 持锁期间会走特征抽取 → 骨干懒加载,
# 同线程重入必须可重入, 普通 Lock 会自死锁
_lock = threading.RLock()
_backbones: dict = {}           # name -> (model, pretrained)


class AnomalyError(RuntimeError):
    """引擎/记忆库错误 (依赖缺失、bank 不存在等), message 带处置指引。"""


def _banks_root() -> str:
    base = os.environ.get("TIANJUN_DATA_DIR") or os.getcwd()
    root = os.path.join(base, "anomaly_banks")
    os.makedirs(root, exist_ok=True)
    return root


def _bank_dir(bank_id: str) -> str:
    return os.path.join(_banks_root(), bank_id)


# ============================================================
# 骨干注册表与特征抽取
# ============================================================

def preferred_backbone() -> str:
    """当前环境的首选骨干名 (不触发加载)。"""
    explicit = (os.environ.get("TJ_ANOMALY_BACKBONE") or "").strip().lower()
    if explicit in ("resnet18", "dinov2", "dinov2_vits14"):
        return "resnet18" if explicit == "resnet18" else "dinov2_vits14"
    if os.environ.get("TJ_ANOMALY_PRETRAINED", "1") == "0":
        return "resnet18"  # 测试确定性: 强制随机 resnet18
    return "dinov2_vits14"


def _load_resnet18():
    import torchvision
    weights = None
    pretrained = False
    if os.environ.get("TJ_ANOMALY_PRETRAINED", "1") != "0":
        try:
            weights = torchvision.models.ResNet18_Weights.DEFAULT
            pretrained = True
        except Exception:
            weights = None
    try:
        model = torchvision.models.resnet18(weights=weights)
    except Exception as e:
        print(f"[Anomaly] ImageNet 预训练权重不可用 ({e}), 回退随机权重 "
              f"(建议离线预置权重文件提升效果)")
        model = torchvision.models.resnet18(weights=None)
        pretrained = False
    return model, pretrained


def _load_dinov2():
    """DINOv2 ViT-S/14 (Apache 2.0)。torch.hub 加载, 有本地缓存即离线可用。"""
    import torch
    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14",
                           verbose=False)
    return model, True


def _get_backbone(name: str):
    """按名加载骨干 (懒加载缓存)。dinov2 失败时抛错由调用方决定回退。"""
    if name in _backbones:
        return _backbones[name]
    with _lock:
        if name in _backbones:
            return _backbones[name]
        try:
            import torch  # noqa: F401
        except Exception as e:
            raise AnomalyError(f"异常检测依赖 torch 缺失: {e}") from e
        if name == "dinov2_vits14":
            model, pretrained = _load_dinov2()
        elif name == "resnet18":
            model, pretrained = _load_resnet18()
        else:
            raise AnomalyError(f"未知骨干: {name}")
        model.eval()
        for p in model.parameters():
            p.requires_grad_(False)
        _backbones[name] = (model, pretrained)
        print(f"[Anomaly] 骨干就绪: {name} (pretrained={pretrained})")
        return _backbones[name]


def _resolve_backbone(prefer: Optional[str] = None):
    """解析并加载可用骨干: 显式指定失败即报错, auto 时 dinov2→resnet18 回退。"""
    name = (prefer or preferred_backbone())
    if name == "dinov2_vits14":
        try:
            model, pretrained = _get_backbone(name)
            return name, model, pretrained
        except AnomalyError:
            raise
        except Exception as e:
            if prefer:  # 建库时显式点名 dinov2 → 不静默降级
                raise AnomalyError(
                    f"DINOv2 骨干加载失败: {e}; 离线环境请预置 torch.hub 缓存 "
                    f"(~/.cache/torch/hub), 或改用 resnet18") from e
            print(f"[Anomaly] DINOv2 不可用 ({e}), 回退 resnet18")
            name = "resnet18"
    model, pretrained = _get_backbone(name)
    return name, model, pretrained


def _to_tensor(image_bgr: np.ndarray):
    import cv2
    import torch
    img = cv2.resize(image_bgr, (_IMG_SIZE, _IMG_SIZE))
    rgb = img[:, :, ::-1].astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    return torch.from_numpy(np.ascontiguousarray(
        ((rgb - mean) / std).transpose(2, 0, 1)[None]))


def _extract_layers(image_bgr: np.ndarray, backbone_name: str,
                    model) -> list:
    """单图 → 每层 patch 特征矩阵列表 [(N_patch, C), ...] (SuperADD 多层)。"""
    import torch
    import torch.nn.functional as F

    x = _to_tensor(image_bgr)
    with torch.no_grad():
        if backbone_name == "dinov2_vits14":
            # ViT-S/14: 224/14=16 → 每层 (1, 256, 384); 取浅/中/深三层
            feats = model.get_intermediate_layers(x, n=[5, 8, 11],
                                                  reshape=True)
            out = []
            for f in feats:
                f = F.avg_pool2d(f, kernel_size=3, stride=1, padding=1)
                c = f.shape[1]
                out.append(f.permute(0, 2, 3, 1).reshape(-1, c)
                           .numpy().astype(np.float32))
            return out
        # resnet18: layer2 (28×28, 128) + layer3 (14×14, 256) 两层
        f = model.conv1(x)
        f = model.bn1(f)
        f = model.relu(f)
        f = model.maxpool(f)
        f1 = model.layer1(f)
        f2 = model.layer2(f1)
        f3 = model.layer3(f2)
        out = []
        for feat in (f2, f3):
            feat = F.avg_pool2d(feat, kernel_size=3, stride=1, padding=1)
            c = feat.shape[1]
            out.append(feat.permute(0, 2, 3, 1).reshape(-1, c)
                       .numpy().astype(np.float32))
        return out


def _extract_patches_legacy(image_bgr: np.ndarray) -> np.ndarray:
    """v1 老库评分路径: resnet18 layer2+layer3 上采样拼接 (28×28, 384)。"""
    import torch
    import torch.nn.functional as F

    _, model, _ = _resolve_backbone("resnet18")
    x = _to_tensor(image_bgr)
    with torch.no_grad():
        f = model.conv1(x)
        f = model.bn1(f)
        f = model.relu(f)
        f = model.maxpool(f)
        f1 = model.layer1(f)
        f2 = model.layer2(f1)
        f3 = model.layer3(f2)
        f3u = F.interpolate(f3, size=f2.shape[-2:], mode="bilinear",
                            align_corners=False)
        feat = torch.cat([f2, f3u], dim=1)
        feat = F.avg_pool2d(feat, kernel_size=3, stride=1, padding=1)
        c = feat.shape[1]
        patches = feat.permute(0, 2, 3, 1).reshape(-1, c)
    return patches.numpy().astype(np.float32)


def _nn_distances(patches: np.ndarray, bank: np.ndarray,
                  chunk: int = 256) -> np.ndarray:
    """每个 patch 对记忆库的最近邻 L2 距离 (分块算, 控内存)。"""
    import torch
    p = torch.from_numpy(patches)
    b = torch.from_numpy(bank)
    out = []
    for i in range(0, p.shape[0], chunk):
        d = torch.cdist(p[i:i + chunk], b)
        out.append(d.min(dim=1).values)
    return torch.cat(out).numpy()


def _subsample(arr: np.ndarray, rng, limit: int = _MAX_BANK_VECTORS) -> np.ndarray:
    if arr.shape[0] > limit:
        idx = rng.choice(arr.shape[0], limit, replace=False)
        return arr[idx]
    return arr


def _fused_score(layer_patches: list, layer_banks: list) -> tuple:
    """多层评分: 每层最大 patch 距离 → 均值融合。返回 (score, 每层距离列表)。"""
    per_layer_dists = []
    per_layer_max = []
    for p, b in zip(layer_patches, layer_banks):
        d = _nn_distances(p, b)
        per_layer_dists.append(d)
        per_layer_max.append(float(d.max()))
    return float(np.mean(per_layer_max)), per_layer_dists


# ============================================================
# 建库 / 评分 / 管理
# ============================================================

def create_bank(name: str, images_bgr: list,
                bank_id: Optional[str] = None,
                backbone: Optional[str] = None) -> dict:
    """用合格品图片建记忆库 (格式 v2 多层)。返回 bank meta (含建议阈值)。"""
    if not images_bgr:
        raise AnomalyError("至少需要 1 张合格品图片")
    bank_id = bank_id or uuid.uuid4().hex[:12]

    with _lock:
        bb_name, model, pretrained = _resolve_backbone(backbone)
        per_image = [_extract_layers(img, bb_name, model) for img in images_bgr]
    n_layers = len(per_image[0])
    rng = np.random.default_rng(42)

    layer_banks = []
    for li in range(n_layers):
        allp = np.concatenate([pi[li] for pi in per_image], axis=0)
        layer_banks.append(_subsample(allp, rng))

    # 建议阈值: 留一法自评分 (每张训练图对"其余图的库"评分)。训练图的 patch
    # 本身就在整库里 (自距离≈0), 直接对整库自评会把阈值算成 0 —— 必须持出估计。
    if len(per_image) >= 2:
        self_scores = []
        for i, pi in enumerate(per_image):
            other_banks = []
            for li in range(n_layers):
                others = np.concatenate(
                    [q[li] for j, q in enumerate(per_image) if j != i], axis=0)
                other_banks.append(_subsample(others, rng))
            s, _ = _fused_score(pi, other_banks)
            self_scores.append(s)
    else:
        # 单图建库: 每层自身 patch 随机对半分, 一半当查询一半当库 (粗估)
        pi = per_image[0]
        halves_q, halves_b = [], []
        for li in range(n_layers):
            p = pi[li]
            half = rng.permutation(p.shape[0])
            halves_q.append(p[half[::2]])
            halves_b.append(p[half[1::2]])
        s, _ = _fused_score(halves_q, halves_b)
        self_scores = [s]
    mean, std = float(np.mean(self_scores)), float(np.std(self_scores))
    threshold = max(mean + 3.0 * std, mean * 1.2) if mean > 0 else 1.0

    meta = {
        "id": bank_id,
        "name": (name or "").strip() or bank_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "format": 2,
        "backbone": bb_name,
        "num_layers": n_layers,
        "num_images": len(images_bgr),
        "num_vectors": int(sum(b.shape[0] for b in layer_banks)),
        "feature_dims": [int(b.shape[1]) for b in layer_banks],
        "pretrained_backbone": bool(pretrained),
        "threshold": round(threshold, 4),
        "train_score_mean": round(mean, 4),
        "train_score_std": round(std, 4),
    }

    d = _bank_dir(bank_id)
    os.makedirs(d, exist_ok=True)
    # 注意: savez 对不以 .npz 结尾的文件名会自动追加后缀, tmp 名必须以 .npz 收尾
    tmp = os.path.join(d, ".tmp_bank.npz")
    np.savez_compressed(tmp, **{f"bank_{i}": b for i, b in enumerate(layer_banks)})
    os.replace(tmp, os.path.join(d, "bank.npz"))
    tmp_meta = os.path.join(d, ".meta.json.tmp")
    with open(tmp_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp_meta, os.path.join(d, "meta.json"))
    print(f"[Anomaly] 记忆库已建: {bank_id} ({meta['name']}) backbone={bb_name} "
          f"images={meta['num_images']} vectors={meta['num_vectors']}")
    return meta


def _load_bank(bank_id: str):
    d = _bank_dir(bank_id)
    meta_path = os.path.join(d, "meta.json")
    bank_path = os.path.join(d, "bank.npz")
    if not (os.path.isfile(meta_path) and os.path.isfile(bank_path)):
        raise AnomalyError(f"记忆库不存在: {bank_id}")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    data = np.load(bank_path)
    if int(meta.get("format") or 1) >= 2:
        banks = [data[f"bank_{i}"] for i in range(int(meta.get("num_layers") or 1))]
    else:
        banks = [data["bank"]]  # v1 老库
    return meta, banks


def score_image(bank_id: str, image_bgr: np.ndarray,
                threshold: Optional[float] = None,
                with_heatmap: bool = False) -> dict:
    """对单图评分。score 超阈值即 is_anomaly。v1/v2 库均可评。"""
    meta, banks = _load_bank(bank_id)
    fmt = int(meta.get("format") or 1)
    with _lock:
        if fmt >= 2:
            bb_name = meta.get("backbone") or "resnet18"
            _, model, _ = _resolve_backbone(bb_name)
            layer_patches = _extract_layers(image_bgr, bb_name, model)
            score, per_layer_dists = _fused_score(layer_patches, banks)
            all_dists = np.concatenate(per_layer_dists)
        else:
            patches = _extract_patches_legacy(image_bgr)
            dists = _nn_distances(patches, banks[0])
            score = float(dists.max())
            per_layer_dists = [dists]
            all_dists = dists
    thr = float(threshold) if threshold is not None else float(meta["threshold"])
    out = {
        "bank_id": bank_id,
        "score": round(score, 4),
        "threshold": round(thr, 4),
        "is_anomaly": bool(score > thr),
        "mean_patch_score": round(float(all_dists.mean()), 4),
        "backbone": meta.get("backbone") or "resnet18",
    }
    if with_heatmap:
        # 各层距离图重排回方阵 → 上采样到最大层分辨率 → 均值融合
        import cv2
        maps = []
        for d in per_layer_dists:
            side = int(round(len(d) ** 0.5))
            if side * side != len(d):
                continue
            maps.append(d.reshape(side, side))
        if maps:
            target = max(m.shape[0] for m in maps)
            acc = np.zeros((target, target), dtype=np.float64)
            for m in maps:
                if m.shape[0] != target:
                    m = cv2.resize(m.astype(np.float32), (target, target),
                                   interpolation=cv2.INTER_LINEAR)
                acc += m
            hm = acc / len(maps)
            rng_span = float(hm.max() - hm.min()) or 1.0
            out["heatmap"] = np.round((hm - hm.min()) / rng_span, 3).tolist()
    return out


def list_banks() -> list:
    out = []
    root = _banks_root()
    for bank_id in sorted(os.listdir(root)):
        meta_path = os.path.join(root, bank_id, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            continue
    return out


def delete_bank(bank_id: str) -> bool:
    import shutil
    d = _bank_dir(bank_id)
    if not os.path.isdir(d):
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def update_threshold(bank_id: str, threshold: float) -> dict:
    meta, _banks = _load_bank(bank_id)
    meta["threshold"] = round(float(threshold), 4)
    d = _bank_dir(bank_id)
    tmp = os.path.join(d, ".meta.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(d, "meta.json"))
    return meta


def engine_status() -> dict:
    """引擎状态 (骨干可用性), 给 API /anomaly/status 类探针用。"""
    loaded = {k: bool(v) for k, v in _backbones.items()}
    return {
        "preferred_backbone": preferred_backbone(),
        "loaded_backbones": loaded,
        "banks": len(list_banks()),
    }
