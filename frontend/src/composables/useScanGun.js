/**
 * USB 扫码枪 (v3.20): USB 键盘模拟扫码枪 (如 NT-1202W) 的全局接入。
 *
 * 这类枪插上 USB 后系统当它是"键盘", 扫一次码 = 极快敲出条码字符 + 回车。
 * 所以零原生依赖、跨平台最稳的接法就是在桌面端窗口捕获键盘流, 用"输入速度"
 * 把扫码枪和人手打字区分开 (人手打不出这么快的连续串)。
 *
 * 配置归属: USB 扫码枪是「扫码器」的一种设备类型 (device_type='usb_hid'), 配置统一
 * 存在扫码器设备表 (parse_config.usb + channel_id), 由「扫码器→设备管理」里新建/编辑。
 * 本模块启动时拉一次设备列表缓存, 设备配置变更后由编辑框调 refreshScanGunConfig() 刷新。
 *
 * 一把枪两种用途 (扫到的码按规则路由):
 *   - pull: 扫工单标签条码 → 去外部 MES 拉对应工单 (复用工单拉取接口)
 *   - bind: 扫工件条码 → 注入扫码绑定链路 (复用 /scanner/simulate, 走 scan_pair)
 *   - both: 按"工单号识别规则"正则区分, 命中=拉工单, 否则=绑工件
 *
 * 后端零改动 (两条路都复用已有接口; usb_hid 设备后端不建网络连接)。
 */
import { ElNotification } from 'element-plus'
import { pullOrders } from '@/api/gateway'
import { getScannerDevices, simulateScannerScan } from '@/api/scanner'
import { dbg } from '@/utils/debug'

const DEFAULT_CFG = {
  enabled: false, usage: 'pull', pullConnId: null, bindChannelId: 0, orderPattern: '^(JOB|ORD)',
}

// 全局键盘监听用的当前生效配置 (从设备表刷新而来, 避免每次扫码都打 HTTP)
let cached = { ...DEFAULT_CFG }

/** 从扫码器设备表拉一次, 取第一台启用的 USB 扫码枪配置, 刷新全局缓存。 */
export async function refreshScanGunConfig() {
  try {
    const resp = await getScannerDevices()
    const list = resp?.data ?? resp ?? []
    const usb = (Array.isArray(list) ? list : []).find(
      d => d.device_type === 'usb_hid' && d.enabled)
    if (usb) {
      const u = (usb.parse_config || {}).usb || {}
      cached = {
        enabled: true,
        usage: u.usage || 'pull',
        pullConnId: u.pull_conn_id ?? null,
        bindChannelId: usb.channel_id || 0,
        orderPattern: u.order_pattern || '^(JOB|ORD)',
      }
    } else {
      cached = { ...DEFAULT_CFG }
    }
  } catch {
    // 拉不到 (未登录/后端未起) 时不动 cached, 静默
  }
  return cached
}

/**
 * 纯路由判断 (无副作用, 便于单测): 一条码该去拉工单还是绑工件。
 * 返回 'pull' | 'bind'。
 */
export function routeCode(cfg, code) {
  const usage = cfg?.usage || 'pull'
  if (usage === 'pull') return 'pull'
  if (usage === 'bind') return 'bind'
  try {
    return new RegExp(cfg.orderPattern || '^(JOB|ORD)').test(code) ? 'pull' : 'bind'
  } catch {
    return 'pull'  // 正则写错时退化为拉工单, 不静默吞码
  }
}

// ============================================================
// 全局键盘捕获 (扫码枪特征: 极快连续字符 + Enter 结尾)
// ============================================================
let started = false
let buffer = ''
let segStart = 0
let lastTime = 0
let busy = false

const CHAR_GAP_MS = 100   // 相邻字符间隔超此值 = 人在打字, 重置缓冲
const SEG_MAX_MS = 800    // 整段(首字符→回车)超此值 = 不是扫码枪
const MIN_LEN = 3         // 条码至少这么长才认 (防误触)

async function onKeydown(e) {
  if (!cached.enabled) return
  const now = Date.now()

  if (e.key === 'Enter') {
    const code = buffer.trim()
    const fast = code.length >= MIN_LEN && (now - segStart) <= SEG_MAX_MS
    buffer = ''
    if (fast) {
      e.preventDefault()
      e.stopPropagation()
      await dispatch(cached, code)
    }
    return
  }

  if (e.key && e.key.length === 1) {
    if (now - lastTime > CHAR_GAP_MS) {
      buffer = e.key
      segStart = now
    } else {
      buffer += e.key
    }
    lastTime = now
  }
}

async function dispatch(cfg, code) {
  if (busy) return
  busy = true
  try {
    const route = routeCode(cfg, code)
    dbg('mes.scanner', 'USB 扫码枪扫到码', `code=${code} 用途=${cfg.usage} → 路由=${route}`)
    if (route === 'pull') await doPull(cfg, code)
    else await doBind(cfg, code)
  } finally {
    busy = false
  }
}

async function doPull(cfg, code) {
  if (!cfg.pullConnId) {
    ElNotification.warning({ title: '扫码拉工单未配置', message: '请在扫码器→USB 扫码枪里选拉取连接', duration: 4000 })
    return
  }
  try {
    const resp = await pullOrders(cfg.pullConnId, { job_no: code, dry_run: false })
    const r = resp?.data ?? resp
    if (r && r.success) {
      ElNotification.success({ title: '扫码拉工单成功', message: `工单 ${code}：新建 ${r.created || 0} / 更新 ${r.updated || 0}`, duration: 3000 })
    } else {
      ElNotification.error({ title: '扫码拉工单失败', message: `工单 ${code}：${(r && r.error) || '未知错误'}`, duration: 5000 })
    }
  } catch (err) {
    dbg('mes.scanner', 'USB 拉工单异常', `${code}: ${err?.response?.data?.detail || err?.message || err}`)
    ElNotification.error({ title: '扫码拉工单异常', message: `工单 ${code}：${err?.response?.data?.detail || err?.message || err}`, duration: 5000 })
  }
}

async function doBind(cfg, code) {
  try {
    const resp = await simulateScannerScan({ barcode: code, channel_id: cfg.bindChannelId || 0 })
    const r = resp?.data ?? resp
    if (r && r.success !== false) {
      ElNotification.success({ title: '扫码绑定工件成功', message: `工位${(cfg.bindChannelId || 0) + 1}：${code}`, duration: 2500 })
    } else {
      ElNotification.error({ title: '扫码绑定失败', message: `${code}：${(r && r.error_msg) || (r && r.message) || '未知错误'}`, duration: 5000 })
    }
  } catch (err) {
    dbg('mes.scanner', 'USB 绑工件异常', `${code}: ${err?.response?.data?.detail || err?.message || err}`)
    ElNotification.error({ title: '扫码绑定异常', message: `${code}：${err?.response?.data?.detail || err?.message || err}`, duration: 5000 })
  }
}

export function startScanGun() {
  if (started) return
  started = true
  window.addEventListener('keydown', onKeydown, true)
  refreshScanGunConfig()  // 启动即拉一次设备配置
}

export function stopScanGun() {
  if (!started) return
  started = false
  window.removeEventListener('keydown', onKeydown, true)
}
