#!/bin/bash
set -uo pipefail
TOKEN=$(sed -E 's#https://[^:]+:([^@]+)@.*#\1#' /root/.git-credentials | head -1)
REPO=dinggouhua/glance-config

echo "=== 1) 远端仓库是否已存在 ==="
curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO" -o /tmp/repo.json -w 'HTTP:%{http_code}\n'
python3 -c "
import json
try:
    d=json.load(open('/tmp/repo.json'))
except Exception as e:
    print('parse fail', e); raise SystemExit
if 'full_name' in d:
    print('found:', d['full_name'], '| private =', d['private'], '| empty =', d.get('size')==0, '| default_branch =', d.get('default_branch'))
else:
    print('resp:', json.dumps(d, ensure_ascii=False)[:300])
"

echo "=== 2) 本地状态 ==="
cd /root/glance-config
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$REPO.git"
git remote -v
echo "本地 commit: $(git rev-list --count HEAD) 个 / 文件 $(git ls-files | wc -l) 个"

echo "=== 3) 推送（GIT_TERMINAL_PROMPT=0 防止卡在密码提示）==="
GIT_TERMINAL_PROMPT=0 git push -u origin main 2>&1 | tail -12
PUSH_RC=${PIPESTATUS[0]}
echo "push exit code: $PUSH_RC"

if [ "$PUSH_RC" != "0" ]; then
  echo "!! 推送失败。若是 403 / Write access to repository not granted："
  echo "   fine-grained PAT 只对“已勾选”的仓库有效，新建仓库默认不在授权列表里。"
  echo "   去 https://github.com/settings/tokens → 编辑该 token → Repository access 勾上 glance-config → Save。"
  exit 1
fi

echo "=== 4) 远端校验 ==="
sleep 2
curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO" -o /tmp/repo2.json -w 'repo HTTP:%{http_code}\n'
python3 -c "
import json
d=json.load(open('/tmp/repo2.json'))
print('private =', d['private'], '| default_branch =', d['default_branch'], '| size =', d['size'], 'KB | pushed_at =', d.get('pushed_at'))
"
curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/git/trees/main?recursive=1" -o /tmp/tree.json -w 'tree HTTP:%{http_code}\n'
python3 -c "
import json,subprocess
d=json.load(open('/tmp/tree.json'))
blobs=sorted(t['path'] for t in d['tree'] if t['type']=='blob')
print('远端文件数 =', len(blobs), '| truncated =', d.get('truncated'))
local=subprocess.run(['git','-C','/root/glance-config','ls-files'],capture_output=True,text=True).stdout.split()
print('本地文件数 =', len(local))
print('一致性:', 'PASS（完全一致）' if blobs==sorted(local) else 'DIFF')
if blobs!=sorted(local):
    print('  仅远端有:', sorted(set(blobs)-set(local)))
    print('  仅本地有:', sorted(set(local)-set(blobs)))
"
echo "=== 5) 远端密钥泄漏复核 ==="
curl -s -H "Authorization: token $TOKEN" "https://raw.githubusercontent.com/$REPO/main/glance/glance.yml" -o /tmp/remote_glance.yml -w 'raw HTTP:%{http_code}\n'
echo "远端 glance.yml 行数: $(wc -l < /tmp/remote_glance.yml)"
if grep -nE '4cbed67d92e74a157e0f9ab639d7ca09|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}' /tmp/remote_glance.yml; then
  echo "!! 远端含硬编码密钥，需立即处理"
else
  echo "远端 glance.yml 无硬编码密钥；占位符数量: $(grep -c 'JUHE_FOOTBALL_KEY' /tmp/remote_glance.yml) 处 JUHE_FOOTBALL_KEY / 共 $(grep -oE '\$\{[A-Z_]+\}' /tmp/remote_glance.yml | sort -u | tr '\n' ' ')"
fi
echo "远端是否含真实持仓文件: $(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: token $TOKEN" "https://raw.githubusercontent.com/$REPO/main/glance/portfolio.md") （404 = 未入库，符合预期）"
