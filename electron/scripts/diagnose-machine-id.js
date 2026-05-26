#!/usr/bin/env node
/*
 * v3.10.2: 客户机 machineId 现场诊断工具
 *
 * 用法 (Windows):
 *   双击 diagnose-machine-id.bat (内部调 Electron 自带的 node.exe)
 *   或: "<安装目录>\resources\app.asar.unpacked\electron\node\node.exe" diagnose-machine-id.js
 *
 * 用法 (Linux/Mac):
 *   node diagnose-machine-id.js
 *
 * 输出:
 *   - 每个硬件源的实际返回值
 *   - 哪些源被 BAD_VALUES 黑名单拒收
 *   - 最终 machineId
 *   - 强标识源数量 (< 1 表示该机器会与同型号机器撞 ID)
 *
 * 报障时: 把这个脚本输出截图 / 文件发给客户支持工程师
 */

const path = require('path');
const fs = require('fs');

// 加载 license-manager — 兼容打包后位置
const candidates = [
  path.join(__dirname, '..', 'license-manager.js'),
  path.join(__dirname, '..', '..', 'license-manager.js'),
  path.join(process.resourcesPath || '', 'app.asar.unpacked', 'electron', 'license-manager.js'),
];
let LM = null;
for (const p of candidates) {
  if (fs.existsSync(p)) {
    try { LM = require(p); break; } catch (e) { /* 忽略, 试下一个 */ }
  }
}
if (!LM) {
  console.error('ERROR: 找不到 license-manager.js. 请确认本脚本放在 electron/scripts/ 下.');
  console.error('已尝试:', candidates);
  process.exit(1);
}

const os = require('os');
const tmpDir = path.join(os.tmpdir(), 'tianjun-machineid-diag-' + Date.now());
fs.mkdirSync(tmpDir, { recursive: true });

console.log('====================================================');
console.log('  天军 AI 视觉检测系统 — machineId 现场诊断');
console.log('====================================================');
console.log(`平台      : ${process.platform}`);
console.log(`Node 版本 : ${process.version}`);
console.log(`主机名    : ${os.hostname()}`);
console.log(`CPU 型号  : ${os.cpus()[0]?.model || '(未知)'}`);
console.log(`临时缓存目录: ${tmpDir}`);
console.log('');

const lm = new LM(tmpDir, '');
const id = lm.getMachineId();
const report = lm.getMachineIdReport();

console.log('====================================================');
console.log('  硬件指纹源采集结果');
console.log('====================================================');
const labels = {
  bbSerial:    '主板序列号 (BaseBoard Serial)',
  uuid:        'BIOS UUID (Computer System UUID)',
  biosSerial:  'BIOS 序列号 (BIOS Serial)',
  diskSerial:  '系统盘序列号 (Disk Serial)',
  mac:         '第一块物理网卡 MAC',
  bbProduct:   '主板型号 [弱标识]',
};
for (const [key, label] of Object.entries(labels)) {
  const value = report.sources[key];
  let status = '';
  if (typeof value === 'string' && value.startsWith('[skipped:')) {
    status = ' ✗ 不可用';
  } else if (value) {
    status = ' ✓';
  }
  console.log(`  ${label.padEnd(40)} : ${value || '(空)'}${status}`);
}

console.log('');
console.log('====================================================');
console.log('  诊断结论');
console.log('====================================================');
console.log(`machineId         : ${id}`);
console.log(`生成质量          : ${report.quality}`);
console.log(`强标识源数量      : ${report.strongSourceCount}`);
console.log('');

if (report.strongSourceCount === 0) {
  console.log('⚠️  严重警告: 没有任何强标识源!');
  console.log('   该机器的 machineId 将与同型号机器撞车 (BIOS 没刷真序列号).');
  console.log('   建议:');
  console.log('   1) 联系工控机厂家在 BIOS 写入真实序列号');
  console.log('   2) 升级到含 TPM 2.0 的硬件');
  console.log('   3) 在 license 里用客户名 + 工位号双重绑定');
} else if (report.strongSourceCount === 1) {
  console.log('⚠️  警告: 只有 1 个强标识源.');
  console.log('   如果该源失效 (例如换硬盘 / 换网卡), machineId 会变化.');
} else {
  console.log(`✓ machineId 唯一性可靠 (${report.strongSourceCount} 个独立强标识源)`);
}

console.log('');
console.log('====================================================');
console.log('  发给技术支持时, 请把完整输出截图或保存为文件');
console.log('====================================================');

// 同时写一个 .txt 文件方便发邮件
try {
  const outFile = path.join(process.cwd(), `machineid-report-${os.hostname()}-${Date.now()}.txt`);
  const lines = [
    `天军 AI 视觉检测系统 — machineId 现场诊断报告`,
    `生成时间: ${new Date().toISOString()}`,
    `平台: ${process.platform}`,
    `主机名: ${os.hostname()}`,
    `CPU: ${os.cpus()[0]?.model || ''}`,
    ``,
    `machineId: ${id}`,
    `生成质量: ${report.quality}`,
    `强标识源数量: ${report.strongSourceCount}`,
    ``,
    `--- 硬件指纹采集详情 ---`,
    JSON.stringify(report.sources, null, 2),
  ];
  fs.writeFileSync(outFile, lines.join('\n'), 'utf-8');
  console.log(`报告文件已写入: ${outFile}`);
} catch (err) {
  console.warn(`(报告文件写入失败: ${err.message})`);
}
