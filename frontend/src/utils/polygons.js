// ==================== 多块 ROI 归一化工具 (2026-09 全系统多块 ROI 改造) ====================
// 与后端 backend/api/source_geometry.py 的 normalize_polygons / normalize_rects 同一套约定:
//   多边形 单块(旧): [[x,y], ...]              —— 元素是点
//   多边形 多块(新): [[[x,y],...], [[x,y],...]] —— 元素是多边形
//   矩形   单块(旧): [x,y,w,h]
//   矩形   多块(新): [[x,y,w,h], ...]
// 序列化约定: 只有 1 块时存回旧格式 (存量客户降级可回退), ≥2 块才落新格式。

/** 单个多边形是否合法: ≥3 点、每点至少 2 个数值 */
function isValidPolygon(poly) {
  if (!Array.isArray(poly) || poly.length < 3) return false;
  return poly.every(
    (p) => Array.isArray(p) && p.length >= 2
      && Number.isFinite(Number(p[0])) && Number.isFinite(Number(p[1])),
  );
}

/**
 * 单块/多块双格式 → 多边形列表 [[[x,y],...], ...]。
 * null / 空 / 完全不合法 → []。坏块剔除。
 */
export function normalizePolygons(raw) {
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const first = raw[0];
  const candidates = (Array.isArray(first) && first.length && Array.isArray(first[0]))
    ? raw          // 多块格式
    : [raw];       // 单块格式
  return candidates.filter(isValidPolygon)
    .map((poly) => poly.map((p) => [Number(p[0]), Number(p[1])]));
}

/** 多边形列表 → 存储格式: 0 块=null, 1 块=旧格式, ≥2 块=新格式 */
export function serializePolygons(polys) {
  if (!Array.isArray(polys) || polys.length === 0) return null;
  return polys.length === 1 ? polys[0] : polys;
}

/** 是否配置了至少一块合法多边形 (替代历史 `poly.length >= 3` 守门) */
export function hasPolygons(raw) {
  return normalizePolygons(raw).length > 0;
}

/** 块数 (显示用) */
export function polygonCount(raw) {
  return normalizePolygons(raw).length;
}

/** 总顶点数 (显示用) */
export function polygonPointCount(raw) {
  return normalizePolygons(raw).reduce((acc, poly) => acc + poly.length, 0);
}

/** 矩形单块/多块双格式 → [[x,y,w,h], ...]; 无有效块 → [] */
export function normalizeRects(raw) {
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const candidates = Array.isArray(raw[0]) ? raw : [raw];
  const out = [];
  for (const r of candidates) {
    if (!Array.isArray(r) || r.length < 4) continue;
    const [x, y, w, h] = r.map(Number);
    if ([x, y, w, h].every(Number.isFinite) && w > 0 && h > 0) out.push([x, y, w, h]);
  }
  return out;
}

/** 矩形列表 → 存储格式: 0 块=null, 1 块=旧格式 [x,y,w,h], ≥2 块=新格式 */
export function serializeRects(rects) {
  if (!Array.isArray(rects) || rects.length === 0) return null;
  return rects.length === 1 ? rects[0] : rects;
}

/** 是否配置了至少一块合法矩形 (替代历史 `roi.length >= 4` 守门) */
export function hasRects(raw) {
  return normalizeRects(raw).length > 0;
}

/** 矩形块数 (显示用) */
export function rectCount(raw) {
  return normalizeRects(raw).length;
}

/** 点是否在单个多边形内 (射线法, 与后端同算法) */
export function pointInPolygon(px, py, poly) {
  let inside = false;
  const n = poly.length;
  let j = n - 1;
  for (let i = 0; i < n; i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if ((yi > py) !== (yj > py)
        && px < ((xj - xi) * (py - yi)) / (((yj - yi) || 1e-18)) + xi) {
      inside = !inside;
    }
    j = i;
  }
  return inside;
}

/** 点是否落在(单块/多块格式)任一多边形内; 无有效块返回 false */
export function pointInAnyPolygon(px, py, raw) {
  return normalizePolygons(raw).some((poly) => pointInPolygon(px, py, poly));
}
