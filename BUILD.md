# 天军科技AI视觉检测系统 - 构建指南

## 概述

本文档说明如何将应用打包为 Windows 安装程序。

## 系统要求

### 构建环境
- **操作系统**: Windows 10/11 64位
- **Node.js**: 18.x 或更高版本
- **Python**: Anaconda/Miniconda (conda 环境名: `tianjun`)
- **NVIDIA 驱动**: 525 或更高版本（用于 GPU 测试）

### 用户环境（安装后）
- **操作系统**: Windows 10/11 64位
- **NVIDIA 驱动**: 525 或更高版本
- **存储空间**: 约 6GB

## 构建步骤

### 方法一：使用自动构建脚本（推荐）

1. 双击运行 `scripts\build-app.bat`
2. 等待构建完成
3. 安装程序位于 `electron\dist\` 目录

### 方法二：手动构建

#### 1. 构建前端

```bash
cd frontend
npm install
npm run build
```

#### 2. 打包 Python 环境

```bash
# 方法 A: 使用脚本
scripts\pack-python-env.bat

# 方法 B: 手动打包
conda activate tianjun
pip install conda-pack
conda-pack -n tianjun -o python-env\tianjun-env.tar.gz
# 解压到 python-env\python 目录
```

#### 3. 构建 Electron 安装程序

```bash
cd electron
npm install
npm run build:win
```

## 目录结构

```
项目根目录/
├── frontend/          # Vue.js 前端代码
│   └── dist/          # 前端构建产物
├── backend/           # FastAPI 后端代码
├── electron/          # Electron 桌面应用
│   ├── main.js        # 主进程
│   ├── preload.js     # 预加载脚本
│   ├── build/         # 构建资源（图标等）
│   └── dist/          # 安装程序输出目录
├── python-env/        # Python 环境
│   └── python/        # conda-pack 解压后的环境
├── scripts/           # 构建脚本
└── best.pt            # YOLO 模型文件
```

## 产物说明

构建完成后，`electron\dist\` 目录包含：

- `天军科技AI视觉检测系统-1.0.0-Setup.exe` - Windows 安装程序
- `win-unpacked/` - 解压版（便携版）

## 安装程序功能

- 支持自定义安装目录
- 创建桌面快捷方式
- 创建开始菜单快捷方式
- 提供卸载程序

## 常见问题

### Q: 构建失败，提示找不到 python-env

A: 请先运行 `scripts\pack-python-env.bat` 打包 Python 环境。

### Q: 安装后无法启动 GPU

A: 请确保已安装 NVIDIA 驱动 525 或更高版本。可以在 NVIDIA 官网下载最新驱动。

### Q: 应用启动很慢

A: 首次启动需要初始化 Python 环境，可能需要 30-60 秒。后续启动会更快。

### Q: 如何更新模型文件

A: 将新的 `.pt` 模型文件复制到安装目录的 `resources\models\` 文件夹，并在应用中重新选择模型。

## 开发调试

在开发模式下运行（不打包）：

```bash
# 终端 1: 启动前端开发服务器
cd frontend
npm run dev

# 终端 2: 启动后端
cd backend
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload

# 终端 3: 启动 Electron
cd electron
npm start
```

## 技术栈

- **前端**: Vue.js 3 + Vite + Element Plus
- **后端**: FastAPI + SQLAlchemy + OpenCV
- **AI**: YOLO (ultralytics) + PyTorch + CUDA 12.1
- **桌面**: Electron 28 + electron-builder
