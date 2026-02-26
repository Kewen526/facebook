#!/bin/bash
# Facebook监控系统 一键部署脚本
# 目标服务器: 47.95.157.46
# 用法: bash deploy.sh

set -e

# ====== 配置 ======
SERVER="root@47.95.157.46"
REMOTE_DIR="/opt/facebook-monitor"
SERVICE_NAME="fb-monitor"
PYTHON="python3"

echo "========================================="
echo "  Facebook监控系统 - 一键部署"
echo "========================================="

# ====== 1. 本地打包 ======
echo ""
echo "[1/5] 打包项目文件..."
TMPFILE=$(mktemp /tmp/fb-deploy-XXXXX.tar.gz)
tar czf "$TMPFILE" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='deploy.sh' \
    -C "$(dirname "$0")" .
echo "  -> 打包完成: $TMPFILE"

# ====== 2. 上传到服务器 ======
echo ""
echo "[2/5] 上传到服务器 $SERVER..."
ssh "$SERVER" "mkdir -p $REMOTE_DIR"
scp "$TMPFILE" "$SERVER:/tmp/fb-deploy.tar.gz"
ssh "$SERVER" "cd $REMOTE_DIR && tar xzf /tmp/fb-deploy.tar.gz && rm -f /tmp/fb-deploy.tar.gz"
rm -f "$TMPFILE"
echo "  -> 上传完成"

# ====== 3. 安装依赖 ======
echo ""
echo "[3/5] 安装Python依赖..."
ssh "$SERVER" "cd $REMOTE_DIR && pip3 install -r requirements.txt -q"
echo "  -> 依赖安装完成"

# ====== 4. 创建systemd服务 ======
echo ""
echo "[4/5] 配置systemd服务..."
ssh "$SERVER" "cat > /etc/systemd/system/${SERVICE_NAME}.service << 'UNIT'
[Unit]
Description=Facebook Monitor System
After=network.target mysql.service

[Service]
Type=simple
WorkingDirectory=${REMOTE_DIR}
ExecStart=/usr/bin/${PYTHON} ${REMOTE_DIR}/app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload"
echo "  -> 服务配置完成"

# ====== 5. 重启服务 ======
echo ""
echo "[5/5] 重启服务..."
ssh "$SERVER" "systemctl restart ${SERVICE_NAME} && systemctl enable ${SERVICE_NAME} 2>/dev/null"
sleep 2
ssh "$SERVER" "systemctl is-active ${SERVICE_NAME}" && STATUS="运行中" || STATUS="启动失败"
echo "  -> 服务状态: $STATUS"

echo ""
echo "========================================="
echo "  部署完成!"
echo "  访问: http://47.95.157.46:8080"
echo "  查看日志: ssh $SERVER journalctl -u $SERVICE_NAME -f"
echo "========================================="
