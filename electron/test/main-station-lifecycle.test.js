'use strict';

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// 原样执行 main.js 的工位开窗、IPC、退出与 webRequest 接线，只模拟进程边界。
// 不启动 Electron/后端、不访问用户配置；不替代物理显示器与 Chromium 网络验收。
const mainPath = path.join(__dirname, '..', 'main.js');
const mainSource = fs.readFileSync(mainPath, 'utf8');
const stationConfig = {
  enabled: true, readonly: false, mapping: { 0: { display_id: 'station' } },
};
const displays = ['primary', 'station', 'hands'].map((id, index) => ({
  id, bounds: { x: index * 1920, y: 0, width: 1920, height: 1080 },
  workArea: { x: index * 1920, y: 0, width: 1920, height: 1040 },
}));

function createHarness({ packaged = true, licensed = true, config = stationConfig } = {}) {
  const userData = path.join('E:', 'CodexTemp', 'electron-station-mock');
  const configPath = path.join(userData, 'workstation_config.json');
  const handlers = new Map();
  const timers = new Map();
  const requests = [];
  const windows = [];
  const exits = [];
  let ready;
  let nextId = 0;
  let timerId = 0;
  let requestListener;
  const session = { webRequest: { onBeforeRequest(filter, listener) {
    requests.push(filter);
    requestListener = listener;
  } } };
  class FakeWindow extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.bounds = { x: 0, y: 0, width: 1600, height: 900, ...options };
      this.visible = false;
      this.destroyed = false;
      this.fullscreen = !!options.fullscreen;
      this.loads = [];
      this.webContents = Object.assign(new EventEmitter(), {
        id: ++nextId, session, reloadCount: 0,
        getURL: () => this.url || '',
        isLoading: () => false,
        openDevTools() {}, stop() {}, send() {},
        reload() { this.reloadCount += 1; },
      });
      windows.push(this);
    }
    setMenuBarVisibility() {}
    getBounds() {
      const { x, y, width, height } = this.bounds;
      return { x, y, width, height };
    }
    setBounds(bounds) { this.bounds = { ...bounds }; }
    setFullScreen(value) { this.fullscreen = value; }
    isFullScreen() { return this.fullscreen; }
    isDestroyed() { return this.destroyed; }
    isVisible() { return this.visible; }
    isMinimized() { return !!this.minimized; }
    setSkipTaskbar(value) { this.skipTaskbar = value; }
    minimize() { this.minimized = true; }
    restore() { this.minimized = false; }
    show() { this.visible = true; }
    hide() { this.visible = false; }
    focus() {}
    loadURL(url) { this.url = url; this.loads.push({ url }); return Promise.resolve(); }
    loadFile(file, options) {
      this.url = `file:///${file}#${options?.hash || ''}`;
      this.loads.push({ file, options });
      return Promise.resolve();
    }
    close() {
      const event = { prevented: false, preventDefault() { this.prevented = true; } };
      this.emit('close', event);
      if (!event.prevented) this.destroy();
    }
    destroy() { this.destroyed = true; this.emit('closed'); }
  }
  class FakeBackend extends EventEmitter {
    constructor() { super(); this.isRunning = false; this.stopCount = 0; }
    async start() { this.isRunning = true; }
    async stop() { this.isRunning = false; this.stopCount += 1; }
    forceKillSync() { this.isRunning = false; }
  }
  class FakeLicense {
    tryAutoInstall() {}
    verify() { return { valid: licensed, message: 'test license' }; }
    getMachineId() { return 'test-machine'; }
  }
  const app = Object.assign(new EventEmitter(), {
    isPackaged: packaged,
    commandLine: { appendSwitch() {} },
    requestSingleInstanceLock: () => true,
    whenReady: () => ({ then(callback) { ready = callback; } }),
    getPath: () => userData,
    getVersion: () => 'test',
    quit() { exits.push('quit'); },
    exit(code) { exits.push(code); },
  });
  const ipcMain = Object.assign(new EventEmitter(), {
    handle(name, callback) { handlers.set(name, callback); },
  });
  const electron = {
    app, BrowserWindow: FakeWindow, ipcMain,
    dialog: { showErrorBox() { throw new Error('Unexpected error dialog'); } },
    Menu: { setApplicationMenu() {} },
    screen: {
      getPrimaryDisplay: () => displays[0],
      getAllDisplays: () => displays,
      getDisplayMatching: (bounds) => displays.find((display) => (
        bounds.x >= display.bounds.x && bounds.x < display.bounds.x + display.bounds.width
      )) || displays[0],
    },
  };
  const dependencies = {
    electron, path,
    fs: {
      existsSync: (file) => file === configPath,
      readFileSync: (file) => {
        assert.equal(file, configPath);
        return JSON.stringify({ channel_count: 2, multi_monitor: config });
      },
    },
    http: {},
    child_process: { execSync() {} },
    './backend-manager': FakeBackend,
    './license-manager': FakeLicense,
    './multi-monitor': require('../multi-monitor'),
  };
  const context = vm.createContext({
    __dirname: path.dirname(mainPath), Buffer, URL, URLSearchParams,
    console: { log() {}, warn() {}, error() {} },
    process: {
      platform: 'win32', pid: 1, env: {}, resourcesPath: userData,
      memoryUsage: () => ({ rss: 0, heapUsed: 0, heapTotal: 0 }),
    },
    require(name) {
      assert.ok(Object.hasOwn(dependencies, name), `Unexpected require: ${name}`);
      return dependencies[name];
    },
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(`${mainSource}\n;globalThis.testAccess = {
    get main() { return mainWindow; },
    get stations() { return stationWindows; },
    get backend() { return backendManager; },
    setLicensed(value) { isLicensed = value; },
    finishShutdown,
  };`, context, { filename: mainPath });
  const access = context.testAccess;
  return {
    access, app, handlers, windows, exits, requests,
    boot: () => ready(),
    apply: (value, sender = access.main.webContents) => handlers.get('multi-monitor:apply')({ sender }, value),
    redirect(webContentsId, url) {
      let result;
      requestListener({ webContentsId, url }, (value) => { result = value; });
      return result;
    },
    runTimers(delay) {
      for (const [id, timer] of [...timers]) {
        if (timer.delay === delay) { timers.delete(id); timer.callback(); }
      }
    },
  };
}


for (const packaged of [true, false]) {
  test((packaged ? '打包' : '开发') + '启动创建可操作工位窗，总控保持普通路由', async () => {
    const h = createHarness({ packaged });
    await h.boot();
    const station = h.access.stations.get('0:main').window;
    assert.equal(h.access.stations.size, 1);
    assert.match(station.url, /#\/monitor\?channel=0&kiosk=1&readonly=0&multi_monitor=1$/);
    assert.doesNotMatch(h.access.main.url.split('#')[1] || '', /kiosk|station_view/);
    assert.equal(station.options.frame, false);
    assert.equal(station.options.closable, false);
    station.emit('ready-to-show');
    assert.deepEqual(station.getBounds(), displays[1].bounds);
    assert.equal(station.fullscreen, true);
    assert.equal(station.visible, true);
    const count = h.windows.length;
    h.apply(stationConfig);
    assert.equal(h.windows.length, count);
    assert.equal(h.access.stations.get('0:main').window, station);
  });
}

test('只读设置热应用重建工位窗，手部与投影窗不受切换影响', async () => {
  const config = { enabled: true, readonly: true, mapping: {
    0: { display_id: 'station', aux_hands_enabled: true, aux_display_id: 'hands' },
    1: { role: 'projection', bounds: { x: 7000, y: 0, width: 1024, height: 768 } },
  } };
  const h = createHarness({ config });
  await h.boot();
  const readonlyWindow = h.access.stations.get('0:main').window;
  const hands = h.access.stations.get('0:aux').window;
  const projection = h.access.stations.get('1:main').window;
  assert.match(readonlyWindow.url, /readonly=1/);
  assert.match(hands.url, /readonly=1.*hands_crop=1/);
  h.apply({ ...config, readonly: false });
  const writableWindow = h.access.stations.get('0:main').window;
  assert.notEqual(writableWindow, readonlyWindow);
  assert.equal(readonlyWindow.isDestroyed(), true);
  assert.match(writableWindow.url, /readonly=0/);
  assert.equal(h.access.stations.get('0:aux').window, hands);
  assert.equal(h.access.stations.get('1:main').window, projection);
  h.apply(config);
  assert.match(h.access.stations.get('0:main').window.url, /readonly=1/);
});

test('OS 主屏复用总控保留操作入口，全部副屏只读后仍能修改设置', async () => {
  const config = { enabled: true, readonly: true, mapping: { 0: { display_id: 'primary' } } };
  const h = createHarness({ config });
  await h.boot();
  h.runTimers(50);
  assert.match(h.access.main.url, /station_view=1&readonly=0/);
  h.apply({ ...config, readonly: false });
  h.runTimers(50);
  assert.match(h.access.main.url, /station_view=1&readonly=0/);
});

test('IPC 只接受总控 sender，禁用销毁工位窗并释放旧请求归属', async () => {
  const h = createHarness();
  await h.boot();
  const station = h.access.stations.get('0:main').window;
  assert.equal(h.apply({ enabled: false }, station.webContents).ok, false);
  assert.equal(h.access.stations.size, 1);
  assert.equal(h.apply({ enabled: false }).ok, true);
  assert.equal(station.isDestroyed(), true);
  assert.equal(h.access.stations.size, 0);
  assert.equal(h.redirect(station.webContents.id, 'http://localhost:8001/video_feed').redirectURL, undefined);
  assert.equal(h.access.main.isDestroyed(), false);
});

test('旧 visitor 字段忽略，空映射不新增窗口或请求监听', async () => {
  const config = { enabled: true, mapping: {}, visitor_display_id: 'hands', visitor_bounds: displays[2].bounds };
  const h = createHarness({ config });
  await h.boot();
  assert.equal(h.access.stations.size, 0);
  assert.equal(h.requests.length, 0);
  assert.equal(h.windows.filter(window => !window.isDestroyed()).length, 1);
  assert.equal(h.access.main.isVisible(), true);
  assert.equal(h.apply(config).ok, true);
  assert.equal(h.access.stations.size, 0);
});

test('实际 webRequest 保留工位流隔离，窗口重建不重复注册监听', async () => {
  const h = createHarness();
  await h.boot();
  const station = h.access.stations.get('0:main').window;
  const oldUrl = 'http://localhost:8001/video_feed?channel=1&viewer=main&viewer=station';
  const result = new URL(h.redirect(station.webContents.id, oldUrl).redirectURL);
  assert.equal(result.searchParams.get('channel'), '1');
  assert.deepEqual(result.searchParams.getAll('viewer'), ['station']);
  assert.equal(h.redirect(station.webContents.id, result.href).redirectURL, undefined);
  for (const id of [h.access.main.webContents.id, -1]) {
    assert.equal(h.redirect(id, oldUrl).redirectURL, undefined);
  }
  h.apply({ enabled: true, mapping: { 0: { display_id: '', bounds: { x: 6000, y: 0, width: 1000, height: 700 } } } });
  const replacement = h.access.stations.get('0:main').window;
  assert.notEqual(replacement, station);
  assert.equal(station.isDestroyed(), true);
  assert.equal(h.requests.length, 1);
  assert.equal(h.redirect(station.webContents.id, oldUrl).redirectURL, undefined);
  assert.match(h.redirect(replacement.webContents.id, oldUrl).redirectURL, /viewer=station/);
});

test('工位流隔离保留；手部 snapshot、投影与总控不重定向', async () => {
  const h = createHarness({ config: { enabled: true, mapping: {
    0: { display_id: 'station', aux_display_id: 'hands', aux_hands_enabled: true },
  } } });
  await h.boot();
  const station = h.access.stations.get('0:main').window;
  const hands = h.access.stations.get('0:aux').window;
  assert.equal(h.requests.length, 1);
  const legacy = 'http://localhost:8016/video_feed?channel=0&t=123';
  assert.match(h.redirect(station.webContents.id, legacy).redirectURL, /viewer=station/);
  for (const window of [hands, h.access.main]) {
    assert.equal(h.redirect(window.webContents.id, legacy).redirectURL, undefined);
  }
  for (const path of ['/snapshot?channel=0', '/api/v1/source/status', '/video_feed_extra']) {
    assert.equal(h.redirect(station.webContents.id, 'http://localhost:8016' + path).redirectURL, undefined);
  }
  h.apply({ enabled: true, mapping: { 0: { display_id: 'station', role: 'projection' } } });
  const projection = h.access.stations.get('0:main').window;
  assert.match(projection.url, /#\/projection\?/);
  assert.equal(h.redirect(projection.webContents.id, legacy).redirectURL, undefined);
  assert.equal(h.redirect(station.webContents.id, legacy).redirectURL, undefined);
  assert.equal(h.requests.length, 1);
});

test('未授权启动不创建工位检测窗；许可守门重新应用时清理已存在工位检测窗', async () => {
  const unlicensed = createHarness({ licensed: false });
  await unlicensed.boot();
  assert.equal(unlicensed.access.stations.size, 0);
  assert.equal(unlicensed.apply(stationConfig).ok, false);
  assert.equal(unlicensed.requests.length, 0);
  const h = createHarness();
  await h.boot();
  const station = h.access.stations.get('0:main').window;
  h.access.setLicensed(false);
  assert.equal(h.apply(stationConfig).ok, false);
  assert.equal(station.isDestroyed(), true);
  assert.equal(h.access.stations.size, 0);
});

for (const mode of ['before-quit', 'finishShutdown', 'backend-stopped']) {
  test(`${mode} 实际退出路径清理工位检测窗`, async () => {
    const h = createHarness();
    await h.boot();
    const station = h.access.stations.get('0:main').window;
    if (mode === 'before-quit') {
      await h.app.listeners('before-quit')[0]({ preventDefault() {} });
    } else if (mode === 'finishShutdown') {
      await h.access.finishShutdown();
    } else {
      h.access.backend.isRunning = false;
      h.access.main.close();
    }
    assert.equal(station.isDestroyed(), true);
    assert.equal(h.access.stations.size, 0);
    assert.ok(h.exits.length > 0);
  });
}

test('工位 renderer 连续崩溃仅重载两次，禁用后排队重载不再执行', async () => {
  const h = createHarness();
  await h.boot();
  const station = h.access.stations.get('0:main').window;
  for (let i = 0; i < 3; i += 1) {
    station.webContents.emit('render-process-gone', {}, { reason: 'crashed' });
    h.runTimers(1000);
  }
  assert.equal(station.webContents.reloadCount, 2);
  h.apply({ enabled: false });
  h.apply(stationConfig);
  const replacement = h.access.stations.get('0:main').window;
  replacement.webContents.emit('render-process-gone', {}, { reason: 'crashed' });
  h.apply({ enabled: false });
  h.runTimers(1000);
  assert.equal(replacement.webContents.reloadCount, 0);
});
