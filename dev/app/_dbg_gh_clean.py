"""清理:删除测试 release"""
import requests
TOKEN = open(r'E:\软件开发\云集智能视频创意站\dev\app\.github_token', encoding='utf-8').read().strip()
H = {'Authorization': f'Bearer {TOKEN}', 'User-Agent': 'YunJii/1.0'}

# 1) 删掉 release 336687342 下的所有 asset
r = requests.get('https://api.github.com/repos/yunjii-cn/vi/releases/336687342/assets', headers=H, timeout=15)
for a in r.json():
    print(f'  asset {a["name"]} id={a["id"]}')
    rd = requests.delete(f'https://api.github.com/repos/yunjii-cn/vi/releases/assets/{a["id"]}', headers=H, timeout=15)
    print(f'    delete: {rd.status_code}')

# 2) 删掉 release 本身
rd = requests.delete('https://api.github.com/repos/yunjii-cn/vi/releases/336687342', headers=H, timeout=15)
print(f'Release delete: {rd.status_code}')
