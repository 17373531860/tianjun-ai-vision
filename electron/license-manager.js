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
  try { return execSync(cmd, { timeout: 5000, encoding: 'utf-8' }).trim(); } catch { return ''; }
}

function getStableFingerprint() {
  const parts = [];

  if (process.platform === 'win32') {
    parts.push(execSafe('wmic baseboard get product /value').replace(/Product=/i, '').trim());
    parts.push(execSafe('wmic csproduct get uuid /value').replace(/UUID=/i, '').trim());
  } else {
    parts.push(readFileSafe('/sys/class/dmi/id/board_name'));
    parts.push(readFileSafe('/sys/class/dmi/id/board_vendor'));
    let uuid = readFileSafe('/sys/class/dmi/id/product_uuid');
    if (!uuid) uuid = execSafe('cat /sys/class/dmi/id/product_uuid 2>/dev/null');
    if (!uuid) uuid = execSafe('sudo cat /sys/class/dmi/id/product_uuid 2>/dev/null');
    parts.push(uuid);
  }

  parts.push(os.cpus()[0]?.model || '');

  return parts.filter(Boolean).join('|');
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

    const stableFp = getStableFingerprint();
    const verifyHash = crypto.createHash('sha256').update(stableFp).digest('hex').substring(0, 16);

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
              console.log(`[License] Machine ID (cached, verified): ${this._machineId}`);
              return this._machineId;
            }
          } else {
            try { fs.writeFileSync(verifyFile, verifyHash, 'utf-8'); } catch {}
            this._machineId = cached;
            console.log(`[License] Machine ID (cached, verify file created): ${this._machineId}`);
            return this._machineId;
          }
        }
      }
    } catch {}

    const hash = crypto.createHash('sha256').update(stableFp).digest('hex');
    this._machineId = 'TJ-' + hash.substring(0, 12).toUpperCase();

    console.log(`[License] Stable fingerprint: ${stableFp.substring(0, 80)}...`);
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
