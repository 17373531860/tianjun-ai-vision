# ==================== 规格/产品码 → 检测项目 统一匹配 ====================
# MES 入站(产品码) 与 上银包装线(规格) 共用同一套"取项目"口径, 便于后期合并。
# 取项目优先级 (越靠前越精确, 命中即止):
#   ① 对照表精确命中     mapping[spec]               (老行为, 任何场景最稳)
#   ② 对照表通配符命中   键含 * / ?, fnmatch 匹配     (前缀变/后缀变/中间固定/任意或无分隔符)
#   ③ 自动同名子串       项目名是 spec 的子串, 取最长  (零配置, 默认关; 项目名贴边天然跨位置)
# 设计取向: 与"位置(前/后/中)"和"分隔符(- _ 空格/无)"完全解耦——
#   通配符让客户显式声明任意形状; 子串判据本身不在乎固定段落在哪、有没有分隔符。

import fnmatch
import re
from typing import Any, Dict, Optional, Tuple


def _as_int(v) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return None


def _boundary_ok(spec: str, name: str) -> bool:
    """name 在 spec 中至少有一处出现, 且该处左右是 串首/串尾 或 非字母数字(分隔符)。

    用于"严格边界"档: 防 HG 误吞 HGH20。代价: 无分隔符场景(HGH20001 找 HGH20)
    右侧贴着数字 '0' → 边界不成立 → 命中不了, 故该档默认关。
    """
    for m in re.finditer(re.escape(name), spec):
        i, j = m.start(), m.end()
        left_ok = (i == 0) or (not spec[i - 1].isalnum())
        right_ok = (j == len(spec)) or (not spec[j].isalnum())
        if left_ok and right_ok:
            return True
    return False


def resolve_project_id_by_spec(
    db,
    spec: str,
    mapping: Optional[Dict[str, Any]],
    *,
    match_by_name: bool = False,
    strict_boundary: bool = False,
) -> Tuple[Optional[int], Optional[str]]:
    """规格/产品码 → (project_id, 命中方式文案)。全未命中 → (None, None)。

    match_by_name 默认 False → 仅认对照表(精确+通配符), 存量客户零差异。
    strict_boundary 仅在 match_by_name=True 的"自动同名子串"档生效。
    """
    spec = str(spec or "").strip()
    if not spec:
        return None, None
    mapping = mapping or {}

    # ① 对照表精确
    if spec in mapping:
        return _as_int(mapping[spec]), "对照表"

    # ② 对照表通配符 (取最长键 = 最具体, 压住 '*' 与 'HGH20-*' 的歧义)
    glob_keys = sorted(
        (k for k in mapping if isinstance(k, str) and ("*" in k or "?" in k)),
        key=len, reverse=True,
    )
    for k in glob_keys:
        if fnmatch.fnmatchcase(spec, k):
            return _as_int(mapping[k]), "通配符对照表"

    # ③ 自动同名子串 (默认关)
    if match_by_name:
        from backend.models.models import Project
        projects = db.query(Project).all()
        # 精确同名优先 (同名多个取 id 最大 = 最新)
        exact = [p for p in projects if (p.name or "") == spec]
        if exact:
            return max(exact, key=lambda p: p.id).id, "项目名精确"
        cands = []
        for p in projects:
            nm = (p.name or "").strip()
            if not nm or nm not in spec:
                continue
            if strict_boundary and not _boundary_ok(spec, nm):
                continue
            cands.append(p)
        if cands:
            # 取项目名最长(最具体); 同长再取 id 大(最新)
            best = max(cands, key=lambda p: (len(p.name or ""), p.id))
            return best.id, "项目名子串"

    return None, None
