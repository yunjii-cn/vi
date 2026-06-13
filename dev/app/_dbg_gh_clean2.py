"""清理:删除测试 release 和 asset"""
import requests
TOKEN = open(r'E:\软件开发\云集智能视频创意站\dev\app\.github_token', encoding='utf-8').read().strip()
H = {'Authorization': f'Bearer {TOKEN}', 'User-Agent': 'YunJii/1.0'}

# 删掉 release 336691748(会自动删除其所有 asset)
rd = requests.delete('https://api.github.com/repos/yunjii-cn/vi/releases/336691748', headers=H, timeout=15)
print(f'Release 336691748 delete: {rd.status_code}')

# 也确认下 release 列表
r = requests.get('https://api.github.com/repos/yunjii-cn/vi/releases?per_page=5', headers=H, timeout=15)
for rel in r.json():
    print(f'  - {rel["tag_name"]}  id={rel["id"]}  assets={len(rel.get("assets", []))}')
