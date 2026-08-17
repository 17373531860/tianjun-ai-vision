'use strict';

// 手工现场诊断：必须用 Electron 二进制运行，不能用 node 直接运行。
// PowerShell: .\node_modules\.bin\electron.cmd .\test\display-smoke.js
const { app, screen } = require('electron');

app.whenReady().then(() => {
  const primaryId = screen.getPrimaryDisplay().id;
  const displays = screen.getAllDisplays().map((display) => ({
    id: String(display.id),
    primary: display.id === primaryId,
    bounds: display.bounds,
    workArea: display.workArea,
    scaleFactor: display.scaleFactor,
  }));
  process.stdout.write(`${JSON.stringify(displays, null, 2)}\n`);
  app.quit();
});
