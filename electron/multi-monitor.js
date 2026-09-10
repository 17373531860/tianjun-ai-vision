'use strict';

const MAX_CHANNELS = 64;

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function normalizeBounds(rawBounds) {
  if (!isRecord(rawBounds)) return null;
  const x = Number(rawBounds.x);
  const y = Number(rawBounds.y);
  const width = Number(rawBounds.width);
  const height = Number(rawBounds.height);
  if (![x, y, width, height].every(Number.isFinite)) return null;
  const normalized = {
    x: Math.trunc(x),
    y: Math.trunc(y),
    width: Math.trunc(width),
    height: Math.trunc(height),
  };
  if (normalized.width <= 0 || normalized.height <= 0) return null;
  return normalized;
}

function normalizeDisplayId(rawDisplayId) {
  return (typeof rawDisplayId === 'string' || typeof rawDisplayId === 'number')
    ? String(rawDisplayId).trim()
    : '';
}

function normalizeAuxViewMode(rawViewMode) {
  return String(rawViewMode || '').trim().toLowerCase() === 'fixed' ? 'fixed' : 'follow';
}

function normalizeMultiMonitorConfig(rawConfig) {
  const warnings = [];
  const raw = isRecord(rawConfig) ? rawConfig : {};
  const rawMapping = isRecord(raw.mapping) ? raw.mapping : {};
  const mapping = {};

  for (const [rawChannelId, rawItem] of Object.entries(rawMapping)) {
    const channelId = Number(rawChannelId);
    if (!Number.isInteger(channelId) || channelId < 0 || channelId >= MAX_CHANNELS) {
      warnings.push(`忽略非法工位 ID: ${rawChannelId}`);
      continue;
    }
    if (!isRecord(rawItem)) {
      warnings.push(`工位 ${channelId} 的显示器映射不是对象，已忽略`);
      continue;
    }

    const displayId = normalizeDisplayId(rawItem.display_id);
    const bounds = normalizeBounds(rawItem.bounds);
    if (!displayId && !bounds) {
      warnings.push(`工位 ${channelId} 缺少有效 display_id/bounds，已忽略`);
      continue;
    }

    const auxDisplayId = normalizeDisplayId(rawItem.aux_display_id);
    const auxBounds = normalizeBounds(rawItem.aux_bounds);
    const auxViewMode = normalizeAuxViewMode(rawItem.aux_view_mode);
    const hasInvalidAuxBounds = rawItem.aux_bounds !== undefined
      && rawItem.aux_bounds !== null
      && !auxBounds;
    if (!auxDisplayId && hasInvalidAuxBounds) {
      warnings.push(`工位 ${channelId} 的副屏 aux_bounds 无效，已忽略副屏映射`);
    }

    mapping[String(channelId)] = {
      display_id: displayId,
      ...(bounds ? { bounds } : {}),
      ...((auxDisplayId || auxBounds) ? {
        aux_display_id: auxDisplayId,
        ...(auxBounds ? { aux_bounds: auxBounds } : {}),
        aux_hands_enabled: rawItem.aux_hands_enabled === true,
        aux_view_mode: auxViewMode,
      } : {}),
    };
  }

  return {
    config: {
      enabled: raw.enabled === true,
      readonly: raw.readonly !== false,
      mapping,
    },
    warnings,
  };
}

function toDisplayDto(display, primaryDisplayId, mainWindowDisplayId = null) {
  const bounds = normalizeBounds(display && display.bounds);
  const workArea = normalizeBounds(display && display.workArea) || bounds;
  return {
    id: String(display && display.id !== undefined ? display.id : ''),
    label: String((display && display.label) || ''),
    bounds,
    workArea,
    isPrimary: String(display && display.id) === String(primaryDisplayId),
    isMainWindowDisplay: mainWindowDisplayId !== null
      && String(display && display.id) === String(mainWindowDisplayId),
  };
}

function boundsOverlap(left, right) {
  const normalizedLeft = normalizeBounds(left);
  const normalizedRight = normalizeBounds(right);
  if (!normalizedLeft || !normalizedRight) return false;
  return normalizedLeft.x < normalizedRight.x + normalizedRight.width
    && normalizedLeft.x + normalizedLeft.width > normalizedRight.x
    && normalizedLeft.y < normalizedRight.y + normalizedRight.height
    && normalizedLeft.y + normalizedLeft.height > normalizedRight.y;
}

function displayTargetsOverlap(left, right) {
  if (!left || !right) return false;
  const leftDisplayId = left.display && left.display.id !== undefined
    ? String(left.display.id)
    : '';
  const rightDisplayId = right.display && right.display.id !== undefined
    ? String(right.display.id)
    : '';
  if (leftDisplayId && rightDisplayId && leftDisplayId === rightDisplayId) return true;
  return boundsOverlap(left.bounds, right.bounds);
}

function isWindowOnOccupiedTarget(windowBounds, occupiedTargets) {
  const targets = Array.isArray(occupiedTargets) ? occupiedTargets : [];
  return targets.some((target) => boundsOverlap(windowBounds, target && target.bounds));
}

function stationWindowKey(channelId, role) {
  return `${channelId}:${role}`;
}

function buildStationAssignments(mapping) {
  const assignments = [];
  const normalizedMapping = isRecord(mapping) ? mapping : {};
  for (const [channelKey, item] of Object.entries(normalizedMapping)) {
    if (!isRecord(item)) continue;
    const channelId = Number(channelKey);
    if (!Number.isInteger(channelId) || channelId < 0 || channelId >= MAX_CHANNELS) continue;

    const displayId = normalizeDisplayId(item.display_id);
    const bounds = normalizeBounds(item.bounds);
    if (displayId || bounds) {
      assignments.push({
        key: stationWindowKey(channelId, 'main'),
        channelId,
        role: 'main',
        readonly: false,
        assignment: {
          display_id: displayId,
          ...(bounds ? { bounds } : {}),
        },
      });
    }

    const auxDisplayId = normalizeDisplayId(item.aux_display_id);
    const auxBounds = normalizeBounds(item.aux_bounds);
    const auxViewMode = normalizeAuxViewMode(item.aux_view_mode);
    if (item.aux_hands_enabled === true && (auxDisplayId || auxBounds)) {
      assignments.push({
        key: stationWindowKey(channelId, 'aux'),
        channelId,
        role: 'aux',
        readonly: true,
        auxViewMode,
        assignment: {
          display_id: auxDisplayId,
          ...(auxBounds ? { bounds: auxBounds } : {}),
        },
      });
    }
  }
  return assignments;
}

function filterStationAssignmentsByChannelCount(assignments, rawChannelCount) {
  const source = Array.isArray(assignments) ? assignments : [];
  const channelCount = Number(rawChannelCount);
  if (!Number.isInteger(channelCount) || channelCount < 1 || channelCount > MAX_CHANNELS) {
    return { assignments: [...source], skippedChannelIds: [] };
  }

  const activeAssignments = [];
  const skippedChannelIds = new Set();
  for (const assignment of source) {
    const channelId = Number(assignment && assignment.channelId);
    if (Number.isInteger(channelId) && channelId >= 0 && channelId < channelCount) {
      activeAssignments.push(assignment);
    } else if (Number.isInteger(channelId) && channelId >= channelCount) {
      skippedChannelIds.add(channelId);
    }
  }
  return {
    assignments: activeAssignments,
    skippedChannelIds: [...skippedChannelIds].sort((left, right) => left - right),
  };
}

function sameBounds(left, right) {
  const leftBounds = normalizeBounds(left);
  const rightBounds = normalizeBounds(right);
  return !!leftBounds && !!rightBounds
    && leftBounds.x === rightBounds.x
    && leftBounds.y === rightBounds.y
    && leftBounds.width === rightBounds.width
    && leftBounds.height === rightBounds.height;
}

function partitionResolvedStationAssignments(assignments, primaryDisplay = null) {
  const source = Array.isArray(assignments) ? assignments : [];
  const reusedMainAssignments = [];
  const stationWindowAssignments = [];
  for (const assignment of source) {
    const target = assignment && assignment.target;
    const targetsPrimaryDisplay = !!target && (
      (target.display && target.display.isPrimary === true)
      || (!target.display && primaryDisplay && sameBounds(target.bounds, primaryDisplay.bounds))
    );
    const reuseMainWindow = assignment && assignment.role === 'main'
      && targetsPrimaryDisplay;
    if (reuseMainWindow) reusedMainAssignments.push(assignment);
    else stationWindowAssignments.push(assignment);
  }
  return { reusedMainAssignments, stationWindowAssignments };
}

function resolveDisplayTarget(assignment, displays) {
  if (!isRecord(assignment)) return null;
  const displayId = String(assignment.display_id || '').trim();
  const rememberedBounds = normalizeBounds(assignment.bounds);
  const available = Array.isArray(displays) ? displays : [];

  if (displayId) {
    const byId = available.find((display) => String(display.id) === displayId);
    if (byId) {
      return {
        display: byId,
        bounds: normalizeBounds(byId.bounds),
        source: 'display_id',
        warning: null,
      };
    }
  }

  if (rememberedBounds) {
    const byBounds = available.find((display) => sameBounds(
      normalizeBounds(display.bounds), rememberedBounds,
    )) || available.find((display) => {
      const current = normalizeBounds(display.bounds);
      return current && current.x === rememberedBounds.x && current.y === rememberedBounds.y;
    });
    if (byBounds) {
      return {
        display: byBounds,
        bounds: normalizeBounds(byBounds.bounds),
        source: 'remembered_bounds',
        warning: displayId
          ? `显示器 ID ${displayId} 不存在，已按记忆坐标匹配显示器 ${byBounds.id}`
          : null,
      };
    }

    return {
      display: null,
      bounds: rememberedBounds,
      source: 'manual_bounds',
      warning: displayId
        ? `显示器 ID ${displayId} 不存在，已降级使用手工/记忆 bounds`
        : '未枚举到匹配显示器，已降级使用手工/记忆 bounds',
    };
  }

  return null;
}

function enumerateDisplaysForApply(getDisplays) {
  try {
    return { displays: getDisplays(), warnings: [], error: null };
  } catch (error) {
    return {
      displays: [],
      warnings: ['显示器枚举失败，按记忆/手工 bounds 降级'],
      error: error && error.message ? error.message : String(error),
    };
  }
}

function buildKioskHash(channelId, role, auxViewMode = 'follow') {
  if (role !== 'main' && role !== 'aux') {
    throw new Error(`未知工位窗口角色: ${role}`);
  }
  const base = `/monitor?channel=${encodeURIComponent(channelId)}&kiosk=1&readonly=${role === 'aux' ? '1' : '0'}&multi_monitor=1`;
  return role === 'aux'
    ? `${base}&video_only=1&hands_crop=1&aux_view_mode=${normalizeAuxViewMode(auxViewMode)}`
    : base;
}

function buildMainWindowHash(channelId) {
  return `/monitor?channel=${encodeURIComponent(channelId)}&station_view=1&readonly=0&multi_monitor=1`;
}

function advanceCrashWindow(state, nowMs, windowMs = 60000) {
  const current = isRecord(state) ? state : {};
  const previousCrashAt = Number(current.lastCrashAt) || 0;
  const now = Number(nowMs);
  const outsideWindow = previousCrashAt === 0 || !Number.isFinite(now)
    || now - previousCrashAt > windowMs;
  return {
    reloadAttempts: outsideWindow ? 1 : (Number(current.reloadAttempts) || 0) + 1,
    lastCrashAt: Number.isFinite(now) ? now : Date.now(),
  };
}

function isMainRenderer(sender, mainWebContents) {
  return !!sender && !!mainWebContents && sender === mainWebContents;
}

module.exports = {
  MAX_CHANNELS,
  advanceCrashWindow,
  boundsOverlap,
  buildKioskHash,
  buildMainWindowHash,
  buildStationAssignments,
  displayTargetsOverlap,
  enumerateDisplaysForApply,
  filterStationAssignmentsByChannelCount,
  isMainRenderer,
  isWindowOnOccupiedTarget,
  normalizeBounds,
  normalizeMultiMonitorConfig,
  partitionResolvedStationAssignments,
  resolveDisplayTarget,
  stationWindowKey,
  toDisplayDto,
};
