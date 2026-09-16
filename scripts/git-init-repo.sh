#!/bin/bash
set -e
cd /root/glance-config
git init -q -b main 2>/dev/null || git init -q
git config user.name "dinggouhua"
git config user.email "drh19960504@gmail.com"
git add -A
echo "=== 待提交文件 ==="
git status --short
echo "=== 安全检查：仓库内是否残留密钥 ==="
if grep -rInE 'ghp_|github_pat_|sk-[A-Za-z0-9]{16,}|4cbed67d92e74a157e0f9ab639d7ca09' . --exclude-dir=.git | grep -v 'JUHE_FOOTBALL_KEY=' ; then
  echo "!! 发现疑似硬编码密钥，已中止"; exit 1
else
  echo "未发现硬编码密钥"
fi
git commit -q -m "chore: 初始化 Glance 工作台配置（glance.yml / Caddyfile / 适配器 / systemd），密钥全部参数化为环境变量"
git log --oneline
echo "文件数: $(git ls-files | wc -l)"