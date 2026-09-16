#!/bin/bash
set -e
TOKEN=$(sed -E 's#https://[^:]+:([^@]+)@.*#\1#' /root/.git-credentials | head -1)

echo "=== 1) 创建私有仓库 ==="
curl -s -X POST -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github+json" \
  https://api.github.com/user/repos \
  -d '{"name":"glance-config","private":true,"description":"自托管 Glance 个人工作台配置与本地适配器（裸机 systemd，密钥参数化）","has_issues":true,"has_wiki":false}' \
  -o /tmp/gh_create.json -w 'HTTP:%{http_code}\n'
python3 -c "
import json
d=json.load(open('/tmp/gh_create.json'))
if 'full_name' in d:
    print('created:', d['full_name'], 'private=', d['private'], d['html_url'])
else:
    print('resp:', json.dumps(d, ensure_ascii=False)[:400])
"

echo "=== 2) 配置 remote 并推送 ==="
cd /root/glance-config
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/dinggouhua/glance-config.git"
git push -u origin main 2>&1 | tail -5

echo "=== 3) 远端校验 ==="
curl -s -H "Authorization: token $TOKEN" https://api.github.com/repos/dinggouhua/glance-config -o /tmp/gh_repo.json -w 'repo HTTP:%{http_code}\n'
python3 -c "
import json
d=json.load(open('/tmp/gh_repo.json'))
print('private =', d['private'], '| default_branch =', d['default_branch'], '| size(KB) =', d['size'])
"
curl -s -H "Authorization: token $TOKEN" 'https://api.github.com/repos/dinggouhua/glance-config/git/trees/main?recursive=1' -o /tmp/gh_tree.json -w 'tree HTTP:%{http_code}\n'
python3 -c "
import json
d=json.load(open('/tmp/gh_tree.json'))
blobs=[t['path'] for t in d['tree'] if t['type']=='blob']
print('远端文件数 =', len(blobs))
for p in sorted(blobs): print('  ', p)
print('truncated =', d.get('truncated'))
"