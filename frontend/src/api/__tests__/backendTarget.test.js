/**
 * 后端地址解析回归 —— 一拖多的命门。
 *
 * 血泪点：老逻辑在非桌面环境硬编码 http://localhost:8001。一体机浏览器打开
 * http://<工作站IP>:8001 后，所有 API 都被打到一体机自己那台没有后端的机器上，
 * 整页 Network Error。这组用例就是钉死"浏览器一律同源"这条判据。
 */
import { describe, expect, it } from 'vitest';

import {
  DEFAULT_BACKEND_HOST,
  DEV_BACKEND_PORT,
  isDesktopContext,
  resolveApiBaseURL,
  resolveBackendHost,
} from '../backendTarget';

const ELECTRON_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Electron/32.0.0 Safari/537.36';

describe('isDesktopContext', () => {
  it('file:// 页面是桌面壳', () => {
    expect(isDesktopContext({ href: 'file:///C:/app/dist/index.html' })).toBe(true);
  });

  it('Electron userAgent 是桌面壳', () => {
    expect(isDesktopContext({ href: 'http://localhost:6001/', userAgent: ELECTRON_UA })).toBe(true);
  });

  it('普通浏览器不是桌面壳', () => {
    expect(isDesktopContext({
      href: 'http://192.168.1.10:8001/',
      userAgent: 'Mozilla/5.0 Chrome/120.0.0.0',
    })).toBe(false);
  });
});

describe('resolveApiBaseURL — 一体机浏览器（生产同源）', () => {
  it('用工作站 IP 打开时走同源相对路径', () => {
    expect(resolveApiBaseURL({
      href: 'http://192.168.1.10:8001/',
      protocol: 'http:',
      hostname: '192.168.1.10',
      isDev: false,
    })).toBe('/api/v1');
  });

  it('换 IP / 换端口 / 走 nginx 都还是同源，不需要改前端配置', () => {
    for (const ctx of [
      { href: 'http://10.0.0.5/', hostname: '10.0.0.5', protocol: 'http:' },
      { href: 'http://192.168.30.7:8080/', hostname: '192.168.30.7', protocol: 'http:' },
      { href: 'https://line-a.factory.lan/', hostname: 'line-a.factory.lan', protocol: 'https:' },
    ]) {
      expect(resolveApiBaseURL({ ...ctx, isDev: false })).toBe('/api/v1');
    }
  });

  it('绝不能回落到 localhost（一体机上没有后端）', () => {
    const base = resolveApiBaseURL({
      href: 'http://192.168.1.10:8001/',
      hostname: '192.168.1.10',
      isDev: false,
    });
    expect(base).not.toContain('localhost');
    expect(base).not.toContain('127.0.0.1');
  });
});

describe('resolveApiBaseURL — Electron 出厂壳（行为不变）', () => {
  it('file:// 仍然直连本机 8001', () => {
    expect(resolveApiBaseURL({ href: 'file:///C:/app/dist/index.html' }))
      .toBe(`${DEFAULT_BACKEND_HOST}/api/v1`);
  });

  it('Electron UA 下即使页面是 http 也直连本机 8001', () => {
    expect(resolveApiBaseURL({
      href: 'http://localhost:6001/', userAgent: ELECTRON_UA, isDev: true,
    })).toBe(`${DEFAULT_BACKEND_HOST}/api/v1`);
  });
});

describe('resolveApiBaseURL — Vite 开发服务器', () => {
  it('按页面 hostname 拼后端端口，而不是写死 localhost', () => {
    expect(resolveApiBaseURL({
      href: 'http://127.0.0.1:6001/', protocol: 'http:', hostname: '127.0.0.1', isDev: true,
    })).toBe(`http://127.0.0.1:${DEV_BACKEND_PORT}/api/v1`);
  });

  it('从别的机器访问开发机时，API 指向开发机而不是访问者自己', () => {
    expect(resolveApiBaseURL({
      href: 'http://192.168.1.10:6001/', protocol: 'http:', hostname: '192.168.1.10', isDev: true,
    })).toBe(`http://192.168.1.10:${DEV_BACKEND_PORT}/api/v1`);
  });
});

describe('resolveApiBaseURL — VITE_API_BASE_URL 优先级最高', () => {
  it('显式配置覆盖一切（副本 worktree 调 8002 靠这条）', () => {
    for (const isDev of [true, false]) {
      expect(resolveApiBaseURL({
        envBase: 'http://localhost:8002/api/v1',
        href: 'http://192.168.1.10:8001/', hostname: '192.168.1.10', isDev,
      })).toBe('http://localhost:8002/api/v1');
    }
  });

  it('末尾多余斜杠被裁掉', () => {
    expect(resolveApiBaseURL({ envBase: 'http://host:9000/api/v1///' }))
      .toBe('http://host:9000/api/v1');
  });
});

describe('resolveBackendHost — 视频流 / 快照', () => {
  it('浏览器返回空串 = 同源（开发走 Vite 代理，生产走工作站自己）', () => {
    expect(resolveBackendHost({ href: 'http://192.168.1.10:8001/' })).toBe('');
    expect(resolveBackendHost({ href: 'http://127.0.0.1:6001/' })).toBe('');
  });

  it('桌面壳返回本机后端', () => {
    expect(resolveBackendHost({ href: 'file:///C:/app/dist/index.html' }))
      .toBe(DEFAULT_BACKEND_HOST);
  });

  it('配了绝对 API 地址时从中取 origin（去掉 /api/v1 尾巴）', () => {
    expect(resolveBackendHost({
      envBase: 'http://10.0.0.9:8002/api/v1', href: 'http://127.0.0.1:6001/',
    })).toBe('http://10.0.0.9:8002');
  });

  it('env 配歪了不抛异常，回落到默认判据', () => {
    expect(resolveBackendHost({
      envBase: 'not-a-url', href: 'http://192.168.1.10:8001/',
    })).toBe('');
  });
});

describe('SSR / 无 window 时不炸', () => {
  it('空上下文给出可用默认值', () => {
    expect(resolveApiBaseURL()).toBe('/api/v1');
    expect(resolveBackendHost()).toBe('');
  });
});
