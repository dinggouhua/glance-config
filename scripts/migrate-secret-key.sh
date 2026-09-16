#!/bin/bash
# 把硬编码的聚合足球 API key 迁移到 EnvironmentFile，并部署脱敏后的 glance.yml
set -e
TS=$(date +%Y%m%d-%H%M%S)
ENVF=/etc/systemd/system/glance-waqi.env

KEY=$(grep -oE 'football/rank\?key=[A-Za-z0-9]+' /etc/glance/glance.yml | head -1 | cut -d= -f2)
if [ -z "$KEY" ]; then echo "!! 未找到足球 key，中止"; exit 1; fi

cp -a /etc/glance/glance.yml /etc/glance/glance.yml.bak-$TS
echo "备份: /etc/glance/glance.yml.bak-$TS"

if ! grep -q '^JUHE_FOOTBALL_KEY=' "$ENVF"; then
  printf 'JUHE_FOOTBALL_KEY=%s\n' "$KEY" >> "$ENVF"
  echo "已写入 JUHE_FOOTBALL_KEY 到 $ENVF"
fi
chmod 640 "$ENVF"
ls -l "$ENVF"

cp /root/glance-config/glance/glance.yml /etc/glance/glance.yml
chmod 644 /etc/glance/glance.yml
chown root:root /etc/glance/glance.yml

systemctl daemon-reload
systemctl restart glance
sleep 6
echo "--- status ---"
systemctl is-active glance
echo "--- config 校验 ---"
/usr/local/bin/glance --config /etc/glance/glance.yml config:print >/dev/null 2>/tmp/glance_cfgcheck.txt && echo "config:print OK" || { echo "config:print FAILED"; cat /tmp/glance_cfgcheck.txt; }
echo "--- 最近日志 ---"
journalctl -u glance --since '8 seconds ago' --no-pager | tail -15
echo "--- 足球 widget 取数 ---"
curl -s --max-time 10 'http://127.0.0.1:8080/api/pages/work/content/' -o /tmp/work_content.html -w 'HTTP:%{http_code}\n'
curl -s --max-time 10 'http://127.0.0.1:8080/api/pages/personal/content/' -o /tmp/personal_content.html -w 'HTTP:%{http_code}\n'
echo "widget-error 次数: work=$(grep -c 'widget-error' /tmp/work_content.html) personal=$(grep -c 'widget-error' /tmp/personal_content.html)"
echo "江苏联赛 widget 命中: $(grep -c '江苏省城市足球联赛' /tmp/work_content.html /tmp/personal_content.html | tr '\n' ' ')"
