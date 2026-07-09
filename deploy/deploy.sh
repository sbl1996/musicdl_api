#!/bin/bash
# musicdl_api 一键部署脚本
# 使用方法: bash deploy/deploy.sh
# 需要 sudo 权限的步骤会自动提权，其余在用户环境下执行

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_USER="$(id -un)"
SERVICE_NAME="musicdl-api"

echo "==================================="
echo "  musicdl_api 一键部署"
echo "==================================="

if ! command -v uv >/dev/null 2>&1; then
    echo "错误: 未找到 uv，请先安装 uv。" >&2
    exit 1
fi

echo "[1/2] 创建虚拟环境并安装依赖..."
cd "$PROJECT_DIR"
if [ ! -x .venv/bin/python ]; then
    uv venv --python python
fi
UV_INDEX_URL="${UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}" \
    uv pip install --python .venv/bin/python .
mkdir -p "$PROJECT_DIR/var/downloads"

echo "[2/2] 配置 systemd 服务..."
sed -e "s/__DEPLOY_USER__/$DEPLOY_USER/g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$PROJECT_DIR/deploy/systemd/musicdl-api.service" \
    > /tmp/musicdl-api.service
sudo cp /tmp/musicdl-api.service /etc/systemd/system/musicdl-api.service
rm -f /tmp/musicdl-api.service
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "==================================="
echo "  部署完成！"
echo "==================================="
echo ""
echo "API 地址: http://localhost:8803"
echo "健康检查: http://localhost:8803/health"
echo ""
echo "常用命令："
echo "  查看日志: journalctl -u $SERVICE_NAME -f"
echo "  重启服务: sudo systemctl restart $SERVICE_NAME"
echo "  停止服务: sudo systemctl stop $SERVICE_NAME"
echo ""
