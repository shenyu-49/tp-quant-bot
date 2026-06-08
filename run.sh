#!/bin/bash
# TP钱包量化交易机器人 - 一键启动

cd "$(dirname "$0")"

echo "=========================================="
echo "🤖 TP钱包量化交易机器人"
echo "=========================================="

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装"
    exit 1
fi

# 检查依赖
python3 -c "import flask" 2>/dev/null || {
    echo "📦 安装依赖..."
    pip install pandas numpy requests flask flask-cors --break-system-packages -q
}

# 启动服务
echo ""
echo "🚀 启动后端服务..."
echo "   访问: http://localhost:3001"
echo "   按 Ctrl+C 停止"
echo ""
python3 server.py
