# glance-config

自托管 Glance 个人工作台（https://www.dclaw.top）的配置与本地适配器，做 Git 版本管理。
裸机 systemd 部署，**不使用 Docker**。

```
Internet → Caddy :443/:80 → 127.0.0.1:8080 Glance
                                     ├── 8897  VIX 恐慌指数
                                     ├── 8898  农历纪念日倒计时（node）
                                     ├── 8900  A 股自选股行情
                                     ├── 8901  大乐透开奖
                                     ├── 8903  江苏油价
                                     ├── 8906  AI 中转站余额
                                     ├── 8907  投资组合汇总（读 portfolio.md）
                                     └── 8908  Polymarket 热门盘口
```

## 目录结构

| 路径 | 部署目标 | 说明 |
| --- | --- | --- |
| `glance/glance.yml` | `/etc/glance/glance.yml` | 主配置：Work / Personal 两页，18 个 widget（17 个 custom-api） |
| `glance/portfolio.example.md` | `/etc/glance/portfolio.md` | 持仓数据模板（真实持仓不入库） |
| `caddy/Caddyfile` | `/etc/caddy/Caddyfile` | 反代 + 证书，`/oil-price/*`、`/stock-chart/*`、`/ai-balance/*` 路径分流 |
| `adapters/*.py` | `/opt/*.py` | Python 适配器（`adapter_common.py` 为公共框架） |
| `adapters/lunar-calendar/` | `/opt/lunar-calendar/` | node 倒计时服务（农历/纪念日） |
| `adapters/zh-history-proxy/` | `/opt/zh-history-proxy/` | 中文历史条目代理（备用，未挂 systemd） |
| `systemd/*.service` | `/etc/systemd/system/` | 11 个服务单元 |
| `scripts/install.sh` | — | 一键部署 / 更新 |
| `scripts/migrate-secret-key.sh` | — | 一次性脚本：把硬编码 key 迁到 EnvironmentFile |

## 部署

```bash
git clone <repo> && cd glance-config
sudo bash scripts/install.sh          # 全量（配置 + 适配器 + systemd + 校验）
sudo bash scripts/install.sh config   # 只更新 glance.yml / Caddyfile
sudo bash scripts/install.sh adapters # 只更新 /opt 适配器与 systemd
```

前置：官方 `glance` 二进制在 `/usr/local/bin/glance`、caddy 已装、node 已装、`/etc/systemd/system/glance-waqi.env` 已按 `.env.example` 填写。

## 环境变量（密钥不入库）

配置与适配器只读 `${VAR}` / `os.environ`，真实值放 `EnvironmentFile`。

- `/etc/systemd/system/glance-waqi.env`（640，`glance` / `lottery` / `oil_price` 服务共用）：`WAQI_TOKEN`、`TODOIST_API_TOKEN`、`WEATHER_LOCATION`、`JUHE_FOOTBALL_KEY`、`CLASH_API_SECRET`、`JUHE_OIL_APIKEY`、`JISUAPI_APPKEY`
- `/etc/glance/glance-relay.env`（640）：`RELAY_USER_URL`、`RELAY_API_KEY`、`RELAY_BALANCE_PORT`

改 EnvironmentFile 后必须 `systemctl daemon-reload && systemctl restart <service>`；变量缺失会导致 Glance 配置解析失败（`environment variable XXX not found`）。

## 关键约束（改配置前先读）

1. **每个 page 至少要有一个 `size: full` 列**，否则启动报 `page N must have either 1 or 2 full width columns` 并进入重启循环。
2. **custom-api 硬编码 5s 超时**（Glance 源码常量，不可配）。所有适配器统一走 `/opt/adapter_common.py`：后台线程刷新 + 内存直出 + 失败保留上次成功值，handler 内绝不打上游。
3. **`patch`/`write_file` 改 `/opt/*.py` 会把属主改成 `root:root`**，服务以 `User=glance` 运行会 `Permission denied`。改完必须 `chown root:glance && chmod 640`（`adapter_common.py` 保持 644）。
4. **模板语法**：支持 `if/else if/range/eq/gt/len/printf/concat/now/formatTime`；**不支持 `contains`**；`.Float` 必须与 `0.0` 比较；`{{`/`}}` 必须成对，单括号会 `unexpected EOF`。
5. **验证看服务端渲染结果**，不要看浏览器截图（可能命中缓存）：
   ```bash
   curl -s http://127.0.0.1:8080/api/pages/personal/content/ | grep -c widget-error   # 必须为 0
   ```
6. 财经数据仅作信息展示，不构成任何投资建议；投资有风险，决策需谨慎。

## 已启用 / 未启用

- 已启用：`glance`、`caddy`、`ashares`、`vix`、`oil_price`、`lottery`、`portfolio-summary`、`polymarket-trending`、`lunar-countdown`、`glance-relay-balance`
- 仓库保留但**本机未启用**（孤儿服务，`glance.yml` 无引用）：`aqi.service`(8899)、`market-overview.service`(8905)。需要时手工 `systemctl enable --now`。

## 敏感信息

仓库不含密钥、不含真实持仓。公网面板建议启用 Glance 认证（`auth.secret-key` + `users.*.password-hash`，用 `glance secret:make` / `glance password:hash` 生成）。
