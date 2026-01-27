#!/bin/bash
# Linux 环境下打包 Python 环境的脚本
# 注意：这只能打包 Linux 环境，Windows 环境需要在 Windows 上执行

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$PROJECT_DIR/python-env"

echo "=========================================="
echo "  Python 环境打包脚本 (Linux)"
echo "=========================================="
echo ""

# 激活 conda
source ~/anaconda3/etc/profile.d/conda.sh

# 检查环境是否存在
if ! conda env list | grep -q "tianjun"; then
    echo "错误: conda 环境 'tianjun' 不存在"
    exit 1
fi

echo "1. 正在打包 conda 环境 'tianjun'..."
echo "   这可能需要几分钟时间..."
echo ""

# 创建输出目录
mkdir -p "$OUTPUT_DIR"

# 使用 conda-pack 打包
conda activate tianjun
conda-pack -n tianjun -o "$OUTPUT_DIR/tianjun-env.tar.gz" --force

echo ""
echo "2. 正在解压环境..."
mkdir -p "$OUTPUT_DIR/python"
tar -xzf "$OUTPUT_DIR/tianjun-env.tar.gz" -C "$OUTPUT_DIR/python"

echo ""
echo "3. 正在修复路径..."
cd "$OUTPUT_DIR/python"
source bin/activate
conda-unpack
source bin/deactivate

echo ""
echo "=========================================="
echo "  打包完成！"
echo "=========================================="
echo ""
echo "打包文件: $OUTPUT_DIR/tianjun-env.tar.gz"
echo "解压目录: $OUTPUT_DIR/python"
echo ""
echo "环境大小:"
du -sh "$OUTPUT_DIR/tianjun-env.tar.gz"
du -sh "$OUTPUT_DIR/python"
