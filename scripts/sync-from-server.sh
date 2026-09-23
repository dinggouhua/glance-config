#!/bin/bash
# 把服务器上的 Glance 配置回同步到本仓库（日常把线上改动纳入版本管理）
# 用法：sudo bash scripts/sync-from-server.sh  然后 git diff 检查 → git commit
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== 同步 glance.yml（硬编码 key 自动参数化）==="
sed -E 's#(apis\.juhe\.cn/[a-z/]*\?key=)[A-Za-z0-9]+#\1${JUHE_FOOTBALL_KEY}#g' \
  /etc/glance/glance.yml > "$REPO_DIR/glance/glance.yml"

echo "=== 同步 Caddyfile 与适配器 ==="
cp /etc/caddy/Caddyfile "$REPO_DIR/caddy/Caddyfile"
for f in /opt/*.py; do cp "$f" "$REPO_DIR/adapters/$(basename "$f")"; done
cp /opt/lunar-calendar/countdown.js /opt/lunar-calendar/package.json /opt/lunar-calendar/package-lock.json "$REPO_DIR/adapters/lunar-calendar/" 2>/dev/null || true
cp /opt/zh-history-proxy/zh_history.py "$REPO_DIR/adapters/zh-history-proxy/" 2>/dev/null || true
for u in /etc/systemd/system/{glance,glance-relay-balance,aqi,ashares,jscl-rank,lol-worlds,lottery,lunar-countdown,market-overview,oil_price,polymarket-trending,portfolio-summary,pp-margin,vix}.service; do
  [ -f "$u" ] && cp "$u" "$REPO_DIR/systemd/"
done
# Caddy 的 basic_auth 凭据走 drop-in 注入，unit 本体来自发行版，故只同步 drop-in
mkdir -p "$REPO_DIR/systemd/caddy.service.d"
[ -f /etc/systemd/system/caddy.service.d/override.conf ] && \
  cp /etc/systemd/system/caddy.service.d/override.conf "$REPO_DIR/systemd/caddy.service.d/"
find "$REPO_DIR" -type f -not -path '*/.git/*' -exec chmod 644 {} \;

echo "=== 密钥泄漏自检 ==="
# 凭据文件本身绝不能被复制进仓库
for f in caddy-auth.env glance-auth.env glance-relay.env oilchem-cookies.json glance-waqi.env; do
  if find "$REPO_DIR" -name "$f" -not -path '*/.git/*' | grep -q .; then
    echo "!! 凭据文件 $f 出现在仓库中，请删除"; exit 1
  fi
done
if grep -rInE 'ghp_|github_pat_|4cbed67d92e74a157e0f9ab639d7ca09|b62dc8a5-b881|_member_user_tonken_[A-Za-z0-9+/=]{20,}|19960319Drh|(CADDY_AUTH_HASH|GLANCE_AUTH_HASH|GLANCE_AUTH_SECRET)=\S' "$REPO_DIR" --exclude-dir=.git --exclude=sync-from-server.sh --exclude=push-to-github.sh; then
  echo "!! 发现硬编码密钥，请修正后重新提交"; exit 1
fi
echo "[OK] 同步完成，接下来：cd $REPO_DIR && git status && git diff"
