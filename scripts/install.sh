#!/bin/bash
# Glance 工作台一键部署 / 更新脚本（裸机 systemd，不使用 Docker）
# 用法：
#   sudo bash scripts/install.sh              # 全量部署（配置 + 适配器 + systemd）
#   sudo bash scripts/install.sh config       # 只更新 glance.yml / Caddyfile
#   sudo bash scripts/install.sh adapters     # 只更新 /opt 适配器与 systemd 单元
#
# 前置条件：
#   1) /usr/local/bin/glance 已安装（glanceapp/glance 官方二进制）
#   2) caddy 已安装并运行
#   3) /etc/systemd/system/glance-waqi.env 已按 .env.example 填好（640）
#   4) node 已安装（lunar-countdown.service 需要）
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"
TS=$(date +%Y%m%d-%H%M%S)

need_root() { [ "$(id -u)" = "0" ] || { echo "请用 sudo 运行"; exit 1; }; }

ensure_user() {
  id -u glance >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin glance
  mkdir -p /etc/glance
}

deploy_config() {
  [ -f /etc/glance/glance.yml ] && cp -a /etc/glance/glance.yml "/etc/glance/glance.yml.bak-$TS"
  install -m 644 -o root -g root "$REPO_DIR/glance/glance.yml" /etc/glance/glance.yml
  install -m 644 -o root -g root "$REPO_DIR/caddy/Caddyfile" /etc/caddy/Caddyfile
  # 真实持仓数据不入库，首次部署从示例复制
  if [ ! -f /etc/glance/portfolio.md ] && [ -f "$REPO_DIR/glance/portfolio.example.md" ]; then
    install -m 640 -o root -g glance "$REPO_DIR/glance/portfolio.example.md" /etc/glance/portfolio.md
    echo "!! 已放置 portfolio.example.md 到 /etc/glance/portfolio.md，请填入真实持仓"
  fi
  echo "[OK] glance.yml / Caddyfile 已部署"
}

deploy_adapters() {
  mkdir -p /opt/lunar-calendar /opt/zh-history-proxy
  # 目标权限：普通适配器 640 root:glance（服务以 User=glance 运行）
  for f in "$REPO_DIR"/adapters/*.py; do
    install -m 640 -o root -g glance "$f" "/opt/$(basename "$f")"
  done
  install -m 644 -o root -g root "$REPO_DIR/adapters/adapter_common.py" /opt/adapter_common.py
  install -m 644 -o root -g glance "$REPO_DIR/adapters/lunar-calendar/countdown.js" /opt/lunar-calendar/countdown.js
  install -m 644 -o root -g root "$REPO_DIR/adapters/lunar-calendar/package.json" /opt/lunar-calendar/package.json
  install -m 644 -o root -g root "$REPO_DIR/adapters/lunar-calendar/package-lock.json" /opt/lunar-calendar/package-lock.json
  install -m 644 -o root -g glance "$REPO_DIR/adapters/zh-history-proxy/zh_history.py" /opt/zh-history-proxy/zh_history.py

  if [ ! -d /opt/lunar-calendar/node_modules ]; then
    echo "[..] 安装 lunar-calendar npm 依赖"
    (cd /opt/lunar-calendar && npm ci --omit=dev 2>/dev/null || npm install --omit=dev)
  fi

  for u in "$REPO_DIR"/systemd/*.service; do
    install -m 644 -o root -g root "$u" "/etc/systemd/system/$(basename "$u")"
  done
  systemctl daemon-reload
  echo "[OK] 适配器与 systemd 单元已部署"
}

enable_services() {
  for s in glance caddy ashares vix aqi oil_price lottery portfolio-summary polymarket-trending lunar-countdown glance-relay-balance; do
    systemctl enable --now "$s" >/dev/null 2>&1 && echo "  enabled: $s" || echo "  skip: $s"
  done
}

verify() {
  sleep 5
  echo "--- 服务状态 ---"
  systemctl is-active glance caddy || true
  echo "--- 配置校验 ---"
  /usr/local/bin/glance --config /etc/glance/glance.yml config:print >/dev/null && echo "config OK"
  echo "--- 页面渲染（widget-error 必须为 0）---"
  for p in work personal; do
    curl -s --max-time 10 "http://127.0.0.1:8080/api/pages/$p/content/" -o "/tmp/${p}_content.html" || true
    echo "  $p: widget-error=$(grep -c 'widget-error' "/tmp/${p}_content.html" 2>/dev/null || echo NA)"
  done
  echo "--- 适配器端口 ---"
  ss -ltnp 2>/dev/null | grep -E ':(8897|8898|8900|8901|8903|8906|8907|8908)' || true
}

need_root
case "$TARGET" in
  all)      ensure_user; deploy_config; deploy_adapters; enable_services; verify ;;
  config)   deploy_config; systemctl restart glance caddy; verify ;;
  adapters) deploy_adapters; verify ;;
  *) echo "未知参数：$TARGET（可选 all|config|adapters）"; exit 1 ;;
esac
echo "完成。日志：journalctl -u glance --since '1 minute ago' --no-pager"
