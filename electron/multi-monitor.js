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

    const rawDisplayId = rawItem.display_id;
    const displayId = (typeof rawDisplayId === 'string' || typeof rawDisplayId === 'number')
      ? String(rawDisplayId).trim()
      : '';
    const bounds = normalizeBounds(rawItem.bounds);
    if (!displayId && !bounds) {
      warnings.push(`工位 ${channelId} 缺少有效 display_id/bounds，已忽略`);
      continue;
    }

    mapping[String(channelId)] = {
      display_id: displayId,
      ...(bounds ? { bounds } : {}),
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

function sameBounds(left, right) {
  return !!left && !!right
    && left.x === right.x
    && left.y === right.y
    && left.width === right.width
    && left.height === right.height;
}

/**
 * 主窗口所在显示器必须永久留给总览/操作界面。
 * display_id 匹配用于正常枚举路径，bounds 匹配用于异显坞 ID 漂移或
 * 枚举失败后的记忆/手工 bounds 降级路径。
 */
function isReservedMainDisplayTarget(target, mainWindowDisplay) {
  if (!target || !mainWindowDisplay) return false;
  const targetDisplayId = target.display && target.display.id !== undefined
    ? String(target.display.id)
    : '';
  const mainDisplayId = mainWindowDisplay.id !== undefined
    ? String(mainWindowDisplay.id)
    : '';
  if (targetDisplayId && mainDisplayId && targetDisplayId === mainDisplayId) return true;
  return sameBounds(
    normalizeBounds(target.bounds),
    normalizeBounds(mainWindowDisplay.bounds),
  );
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

function buildKioskHash(channelId, readonly) {
  return `/monitor?channel=${encodeURIComponent(channelId)}&kiosk=1&readonly=${readonly ? '1' : '0'}&multi_monitor=1`;
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
  buildKioskHash,
  enumerateDisplaysForApply,
  isMainRenderer,
  isReservedMainDisplayTarget,
  normalizeBounds,
  normalizeMultiMonitorConfig,
  resolveDisplayTarget,
  toDisplayDto,
};
