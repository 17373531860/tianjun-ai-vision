const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

const PUBLIC_KEY = `-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7pzkErjVtIpWJ3GWqRKi
nPqsg8bG1MRjdHvYux1XLBWCUak1LakrU/WxrDKkyvhuKyA+y7GlT37VwvIpUB1v
An48IdKqk6mp5iPEzBeHx1+68VC8pSrtmFlOK7bRQq9BCmeAWOOcWpbSPJ0xX+kS
50v9v/Pxpv2EhciO0fz/uVuIp9LxlIUF8ho7auDLteGVT1tI8IAGPTQX7ouAYsE/
YWmtpmjetmSWKiscb0uUV74KFBPZyGk2y5a6JX4ELOI+KeWUP4rWcudFo2fNZG3n
hhckBIeqxc1xg3gPONve4C9SibDPvyWt3ULKSrBEFa+JLEbtAsHEhd/PrusjPuMb
mwIDAQAB
-----END PUBLIC KEY-----`;

function readFileSafe(filePath) {
  try { return fs.readFileSync(filePath, 'utf-8').trim(); } catch { return ''; }
}

function execSafe(cmd) {
  try { return execSync(cmd, { timeout: 8000, encoding: 'utf-8' }).trim(); } catch { return ''; }
}

// v3.10.2: 过滤"工控机厂家偷懒"返回的占位字符串. 这些值出现在多台同批次机器上,
// 不能拿来当唯一指纹.
const BAD_VALUES = new Set([
  '',
  '0',
  '00000000-0000-0000-0000-000000000000',
  'ffffffff-ffff-ffff-ffff-ffffffffffff',
  'default string',
  'to be filled by o.e.m.',
  'to be filled by o.e.m',
  'system serial number',
  'system manufacturer',
  'system product name',
  'not specified',
  'not applicable',
  'not available',
  'none',
  'null',
  'undefined',
  'unknown',
  'na',
  'n/a',
  'chassis serial number',
  'baseboard serial number',
  'default',
]);

function isUsable(value) {
  if (!value || typeof value !== 'string') return false;
  const norm = value.trim().toLowerCase();
  if (norm.length < 4) return false;
  return !BAD_VALUES.has(norm);
}

// v3.10.2: Windows 单一 PowerShell 调用一把取回所有需要的字段,
// 比串行 5 次 wmic 快得多 + 不依赖 Win11 24H2+ 已废弃的 wmic 工具.
function probeWindows() {
  const ps = [
    "$ErrorActionPreference='SilentlyContinue';",
    "$bb=Get-CimInstance Win32_BaseBoard;",
    "$cs=Get-CimInstance Win32_ComputerSystemProduct;",
    "$bios=Get-CimInstance Win32_BIOS;",
    "$disk=Get-CimInstance Win32_DiskDrive | Where-Object {$_.MediaType -match 'Fixed' -or $_.InterfaceType -ne 'USB'} | Sort-Object Index | Select-Object -First 1;",
    "$mac=(Get-NetAdapter -Physical | Where-Object Status -ne 'Disabled' | Sort-Object ifIndex | Select-Object -First 1 -ExpandProperty MacAddress);",
    "@{",
    "  bbSerial=$bb.SerialNumber;",
    "  bbProduct=$bb.Product;",
    "  uuid=$cs.UUID;",
    "  biosSerial=$bios.SerialNumber;",
    "  diskSerial=$disk.SerialNumber;",
    "  mac=$mac",
    "} | ConvertTo-Json -Compress"
  ].join(' ');
  try {
    const raw = execSync(`powershell -NoProfile -NonInteractive -Command "${ps.replace(/"/g, '\\"')}"`, {
      timeout: 15000,
      encoding: 'utf-8',
    }).trim();
    return JSON.parse(raw);
  } catch (err) {
    return {};
  }
}

// v3.10.2: 老 wmic 命令兜底 (Windows 10 / Win11 早期版本)
function probeWindowsWmicFallback() {
  return {
    bbSerial: execSafe('wmic baseboard get serialnumber /value').replace(/SerialNumber=/i, '').trim(),
    bbProduct: execSafe('wmic baseboard get product /value').replace(/Product=/i, '').trim(),
    uuid: execSafe('wmic csproduct get uuid /value').replace(/UUID=/i, '').trim(),
    biosSerial: execSafe('wmic bios get serialnumber /value').replace(/SerialNumber=/i, '').trim(),
    diskSerial: execSafe('wmic diskdrive where "Index=0" get serialnumber /value').replace(/SerialNumber=/i, '').trim(),
    mac: '',
  };
}

function probeLinux() {
  const probe = {
    bbSerial: readFileSafe('/sys/class/dmi/id/board_serial'),
    bbProduct: readFileSafe('/sys/class/dmi/id/board_name'),
    uuid: readFileSafe('/sys/class/dmi/id/product_uuid'),
    biosSerial: readFileSafe('/sys/class/dmi/id/product_serial'),
    diskSerial: '',
    mac: '',
  };
  if (!isUsable(probe.uuid)) {
    const sudoUuid = execSafe('sudo -n cat /sys/class/dmi/id/product_uuid 2>/dev/null');
    if (isUsable(sudoUuid)) probe.uuid = sudoUuid;
  }
  // 第一块非 lo / 非虚拟网卡 MAC
  const mac = execSafe(
    "ls /sys/class/net | grep -v -E '^(lo|docker|br-|veth|virbr|tun|tap)' | head -1 | xargs -I{} cat /sys/class/net/{}/address 2>/dev/null"
  );
  if (isUsable(mac)) probe.mac = mac;
  // 系统盘 / 第一块物理盘的型号+序列号 (lsblk 在大多数发行版默认有)
  const disk = execSafe(
    "lsblk -ndo SERIAL,MODEL $(lsblk -no PKNAME $(findmnt -no SOURCE /) 2>/dev/null | head -1 | xargs -I{} echo /dev/{}) 2>/dev/null"
  );
  if (isUsable(disk)) probe.diskSerial = disk;
  return probe;
}

// v3.10.2: 完整 fingerprint pipeline.
// 返回 { fingerprint, parts, weakSources } —— 调用方可以判 weakSources 决定是否报警.
function getStableFingerprint() {
  const probe = process.platform === 'win32' ? probeWindows() : probeLinux();
  // Windows: PowerShell 一把没拿到 → 兜底走老 wmic
  if (process.platform === 'win32') {
    const winCount = ['bbSerial', 'uuid', 'biosSerial', 'diskSerial', 'mac']
      .filter(k => isUsable(probe[k])).length;
    if (winCount === 0) {
      const fb = probeWindowsWmicFallback();
      Object.assign(probe, fb);
    }
  }

  const parts = [];
  const debug = {};
  for (const key of ['bbSerial', 'uuid', 'biosSerial', 'diskSerial', 'mac', 'bbProduct']) {
    const v = (probe[key] || '').toString().trim();
    if (isUsable(v)) {
      parts.push(`${key}:${v}`);
      debug[key] = v;
    } else {
      debug[key] = `[skipped: "${v}"]`;
    }
  }

  // CPU 型号永远加一份, 但**只作弱标识**, 不计入"有效源数量"
  const cpu = os.cpus()[0]?.model || '';
  if (cpu) parts.push(`cpu:${cpu}`);

  return {
    fingerprint: parts.join('|'),
    parts,
    strongCount: parts.filter(p => !p.startsWith('cpu:') && !p.startsWith('bbProduct:')).length,
    debug,
  };
}

class LicenseManager {
  constructor(userDataPath, resourcesPath) {
    this.userDataPath = userDataPath;
    this.resourcesPath = resourcesPath || '';
    this.licensePath = path.join(userDataPath, 'license.lic');
    this._machineId = null;
    this._licenseInfo = null;
  }

  tryAutoInstall() {
    if (fs.existsSync(this.licensePath)) return false;
    const machineId = this.getMachineId();
    const searchDirs = [
      path.join(this.resourcesPath, 'licenses'),
      path.join(this.resourcesPath, '..', 'licenses'),
    ];
    for (const dir of searchDirs) {
      const candidate = path.join(dir, `${machineId}.lic`);
      if (fs.existsSync(candidate)) {
        console.log(`[License] Found pre-installed license: ${candidate}`);
        fs.mkdirSync(path.dirname(this.licensePath), { recursive: true });
        fs.copyFileSync(candidate, this.licensePath);
        return true;
      }
    }
    return false;
  }

  getMachineId() {
    if (this._machineId) return this._machineId;

    const cacheFile = path.join(this.userDataPath, 'machine_id.txt');
    const verifyFile = path.join(this.userDataPath, 'hw_verify.txt');

    // v3.10.2: 调用方需要时可以读 _lastFingerprintReport (诊断用)
    const fp = getStableFingerprint();
    const stableFp = fp.fingerprint;
    this._lastFingerprintReport = fp;
    const verifyHash = crypto.createHash('sha256').update(stableFp).digest('hex').substring(0, 16);

    // v3.10.2 守门: 强标识源 (主板序列号/BIOS UUID/磁盘序列号/网卡 MAC) 必须 ≥ 1 个.
    // 全部失败时只剩 CPU 型号 + 主板型号, 同型号机器会算出同一 machineId (v3.10.1 已知 bug).
    if (fp.strongCount === 0) {
      console.error('[License] !!!! NO STRONG HARDWARE IDENTIFIER AVAILABLE !!!!');
      console.error('[License] All of {boardSerial, biosUuid, biosSerial, diskSerial, mac} are missing or placeholder.');
      console.error('[License] Probe result:', JSON.stringify(fp.debug, null, 2));
      console.error('[License] machineId would collide across same-model machines — refusing to cache.');
      // 仍然返回一个值让上层继续, 但不写缓存 (这样客户机一旦升级到含真序列号的硬件/系统, 自然会拿到正确 ID)
      const hash = crypto.createHash('sha256').update(stableFp).digest('hex');
      this._machineId = 'TJ-' + hash.substring(0, 12).toUpperCase();
      this._machineIdQuality = 'weak';
      return this._machineId;
    }

    try {
      if (fs.existsSync(cacheFile)) {
        const cached = fs.readFileSync(cacheFile, 'utf-8').trim();
        if (cached && cached.startsWith('TJ-') && cached.length >= 10) {
          if (fs.existsSync(verifyFile)) {
            const storedVerify = fs.readFileSync(verifyFile, 'utf-8').trim();
            if (storedVerify !== verifyHash) {
              console.log('[License] Hardware verification mismatch — cache from different machine, regenerating');
            } else {
              this._machineId = cached;
              this._machineIdQuality = 'cached';
              console.log(`[License] Machine ID (cached, verified): ${this._machineId}  [strongSources=${fp.strongCount}]`);
              return this._machineId;
            }
          } else {
            try { fs.writeFileSync(verifyFile, verifyHash, 'utf-8'); } catch {}
            this._machineId = cached;
            this._machineIdQuality = 'cached';
            console.log(`[License] Machine ID (cached, verify file created): ${this._machineId}  [strongSources=${fp.strongCount}]`);
            return this._machineId;
          }
        }
      }
    } catch {}

    const hash = crypto.createHash('sha256').update(stableFp).digest('hex');
    this._machineId = 'TJ-' + hash.substring(0, 12).toUpperCase();
    this._machineIdQuality = 'fresh';

    console.log(`[License] Fingerprint sources: ${JSON.stringify(fp.debug)}`);
    console.log(`[License] Strong source count: ${fp.strongCount}`);
    console.log(`[License] Machine ID (new): ${this._machineId}`);

    try {
      fs.mkdirSync(path.dirname(cacheFile), { recursive: true });
      fs.writeFileSync(cacheFile, this._machineId, 'utf-8');
      fs.writeFileSync(verifyFile, verifyHash, 'utf-8');
      console.log(`[License] Machine ID + verify hash cached`);
    } catch (err) {
      console.warn(`[License] Failed to cache: ${err.message}`);
    }

    return this._machineId;
  }

  // v3.10.2: 暴露指纹诊断报告, 供 main.js / 前端 / 客户支持现场排错
  getMachineIdReport() {
    if (!this._machineId) this.getMachineId();
    return {
      machineId: this._machineId,
      quality: this._machineIdQuality || 'unknown',
      strongSourceCount: this._lastFingerprintReport?.strongCount || 0,
      sources: this._lastFingerprintReport?.debug || {},
    };
  }

  verify() {
    this._licenseInfo = null;

    if (!fs.existsSync(this.licensePath)) {
      return { valid: false, reason: 'no_license', message: '未找到授权文件' };
    }

    try {
      const raw = fs.readFileSync(this.licensePath, 'utf-8');
      const lic = JSON.parse(raw);

      if (!lic.data || !lic.signature) {
        return { valid: false, reason: 'invalid_format', message: '授权文件格式无效' };
      }

      const verifier = crypto.createVerify('SHA256');
      verifier.update(lic.data);
      const sigValid = verifier.verify(PUBLIC_KEY, lic.signature, 'base64');

      if (!sigValid) {
        return { valid: false, reason: 'invalid_signature', message: '授权签名验证失败' };
      }

      const payload = JSON.parse(lic.data);
      const machineId = this.getMachineId();

      if (payload.machineId !== machineId) {
        return {
          valid: false,
          reason: 'machine_mismatch',
          message: `授权与本机不匹配（本机: ${machineId}）`
        };
      }

      if (payload.expiresAt) {
        const expires = new Date(payload.expiresAt);
        if (expires < new Date()) {
          return {
            valid: false,
            reason: 'expired',
            message: `授权已过期（${payload.expiresAt}）`
          };
        }
      }

      this._licenseInfo = payload;
      return {
        valid: true,
        message: '授权验证通过',
        info: {
          customer: payload.customerName || '',
          expiresAt: payload.expiresAt || 'permanent',
          machineId: payload.machineId
        }
      };
    } catch (err) {
      return { valid: false, reason: 'read_error', message: `读取授权文件失败: ${err.message}` };
    }
  }

  importLicense(sourcePath) {
    try {
      const raw = fs.readFileSync(sourcePath, 'utf-8');
      JSON.parse(raw);

      fs.mkdirSync(path.dirname(this.licensePath), { recursive: true });
      fs.copyFileSync(sourcePath, this.licensePath);

      return this.verify();
    } catch (err) {
      return { valid: false, reason: 'import_error', message: `导入失败: ${err.message}` };
    }
  }

  getLicenseInfo() {
    return this._licenseInfo;
  }
}

module.exports = LicenseManager;
