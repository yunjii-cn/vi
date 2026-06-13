"""列出 release 下的 asset"""
import requests
TOKEN = open(r'E:\软件开发\云集智能视频创意站\dev\app\.github_token', encoding='utf-8').read().strip()
H = {'Authorization': f'Bearer {TOKEN}', 'User-Agent': 'YunJii/1.0'}
r = requests.get('https://api.github.com/repos/yunjii-cn/vi/releases/336698455/assets', headers=H, timeout=15)
for a in r.json():
    print(f'  - {a["name"]}  id={a["id"]}')
