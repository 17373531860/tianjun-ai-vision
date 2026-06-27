// ==================== 前端调试日志总线 (v3.17.x) ====================
// 设置页「调试设置」(开发者模式) 的前端半边:
//   - 按类别开关 (localStorage 持久化, 默认全关)
//   - dbg(cat, action, detail): 关闭时一次对象查询即返回, 零开销
//   - 开启时: 写入前端环形缓冲 (3000 条) + 回传后端落盘 (backend-debug.log)
//
// 设计约束: 本模块不 import api/index.js (api 拦截器要反过来 import 本模块,
// 必须避免循环依赖), 回传用原生 fetch。

import { ref } from 'vue';

// ---- 类别目录: key -> { label, group } (调试设置页开关矩阵据此渲染) ----
export const FRONTEND_CATEGORIES = {
  'page.nav':           { label: '页面切换 (进入/离开每个页面)', group: '全局' },
  'api.request':        { label: 'API 请求 (URL/参数/耗时/状态码/报错)', group: '全局' },
  'auth.ops':           { label: '登录鉴权 (登录/登出/权限拒绝)', group: '全局' },
  'app.lifecycle':      { label: '应用生命周期 (后端崩溃自愈/自动恢复)', group: '全局' },
  'monitor.control':    { label: '检测控制按钮 (开始/停止/待机/清零)', group: '实时监控' },
  'monitor.poll':       { label: '状态轮询 (检测结果/状态同步异常)', group: '实时监控' },
  'monitor.video':      { label: '视频流 (MJPEG 连接/断开/重连)', group: '实时监控' },
  'monitor.events':     { label: '事件提示 (OK/NG Toast/语音播报)', group: '实时监控' },
  'project.crud':       { label: '项目操作 (新建/保存/激活/删除/复制)', group: '项目管理' },
  'project.config':     { label: '项目配置 (步骤/事件/ROI/计数器编辑)', group: '项目管理' },
  'model.upload':       { label: '模型上传 (选择/上传/进度/失败)', group: '模型仓库' },
  'model.convert':      { label: '模型转换 (格式转换/状态轮询)', group: '模型仓库' },
  'model.manage':       { label: '模型管理 (删除/标签/详情/刷新)', group: '模型仓库' },
  'source.ops':         { label: '输入源操作 (连接/测试/保存并启动)', group: '输入源' },
  'source.workstation': { label: '多工位 (工位数切换/GPU 分配)', group: '输入源' },
  'data.query':         { label: '数据查询 (筛选/翻页/详情/回放)', group: '数据中心' },
  'data.export':        { label: '数据导出 (CSV/自定义导出)', group: '数据中心' },
  'data.maintain':      { label: '数据维护 (清理/备份)', group: '数据中心' },
  'mes.order':          { label: '工单管理 (CRUD/绑定/状态流转)', group: 'MES' },
  'mes.workpiece':      { label: '工件管理 (查询/详情/判定)', group: 'MES' },
  'mes.defect':         { label: '缺陷管理 (缺陷记录/缺陷码)', group: 'MES' },
  'mes.scanner':        { label: '扫码器 (CRUD/连接/模拟/禁用)', group: 'MES' },
  'mes.gateway':        { label: '推送网关 (连接配置/测试/字段)', group: 'MES' },
  'mes.pull':           { label: '工单拉取 (测试连接/试同步/立即同步)', group: 'MES' },
  'mes.packaging':      { label: '包装箱结算 (配置保存/Monitor卡/扫码路由)', group: 'MES' },
  'mes.cluster':        { label: '集群 (主从配置/心跳/box)', group: 'MES' },
  'mes.external':       { label: '外部设备 (称重器/串口外设)', group: 'MES' },
  'alarm.ops':          { label: '报警设置 (串口连接/测试/规则保存)', group: '报警' },
  'settings.ops':       { label: '系统设置 (各 Tab 保存/插件管理)', group: '设置' },
};

const FLAGS_KEY = 'tianjun:debug_flags';
const BUFFER_MAX = 3000;

// ---- 开关状态 (模块级, 非 Pinia — 避免 store 初始化顺序问题) ----
const _flags = {};
try {
  const saved = JSON.parse(localStorage.getItem(FLAGS_KEY) || '{}');
  Object.keys(FRONTEND_CATEGORIES).forEach(k => { _flags[k] = !!saved[k]; });
} catch { Object.keys(FRONTEND_CATEGORIES).forEach(k => { _flags[k] = false; }); }

// ---- 环形缓冲: 普通数组 + 版本号 ref (避免几千条日志深度响应式拖垮渲染) ----
const _buffer = [];
let _seq = 0;
export const bufferVersion = ref(0);

// ---- 回传后端 (与 api/index.js 同源的 baseURL 计算, 不依赖 axios) ----
const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001/api/v1').replace(/\/+$/, '');

function postToBackend(entry) {
  try {
    fetch(`${API_BASE}/debug/client-log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: entry.category, action: entry.action, detail: entry.detail }),
      keepalive: true,
    }).catch(() => {});
  } catch { /* 回传失败静默, 调试设施不能反噬业务 */ }
}

// ==================== 对外 API ====================

export function dbgOn(category) {
  return !!_flags[category];
}

/**
 * 埋点入口: dbg('monitor.control', '点击开始', 'ch=0 project=OPPO')
 * 类别未开启时一次对象查询直接返回。
 */
export function dbg(category, action, detail = '') {
  if (!_flags[category]) return;
  const now = new Date();
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  const entry = {
    seq: ++_seq,
    ts: `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}.${pad(now.getMilliseconds(), 3)}`,
    category,
    action: String(action).slice(0, 300),
    detail: detail ? String(detail).slice(0, 2000) : '',
    source: 'frontend',
  };
  _buffer.push(entry);
  if (_buffer.length > BUFFER_MAX) _buffer.splice(0, _buffer.length - BUFFER_MAX);
  bufferVersion.value++;
  postToBackend(entry);
}

/** 包一层 try/catch 的错误埋点: 始终记录到对应类别 (供 catch 分支用) */
export function dbgErr(category, action, err) {
  if (!_flags[category]) return;
  const msg = (err && (err.response?.data?.detail || err.message)) || String(err);
  dbg(category, `${action} [失败]`, msg);
}

export function getFlags() {
  return { ..._flags };
}

export function setFlag(category, on) {
  if (!(category in FRONTEND_CATEGORIES)) return;
  _flags[category] = !!on;
  try { localStorage.setItem(FLAGS_KEY, JSON.stringify(_flags)); } catch {}
}

export function setAllFlags(on) {
  Object.keys(FRONTEND_CATEGORIES).forEach(k => { _flags[k] = !!on; });
  try { localStorage.setItem(FLAGS_KEY, JSON.stringify(_flags)); } catch {}
}

export function getFrontendLogs() {
  return _buffer.slice();
}

export function clearFrontendLogs() {
  _buffer.length = 0;
  bufferVersion.value++;
}
