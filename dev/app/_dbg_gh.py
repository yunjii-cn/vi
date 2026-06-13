"""调试:查看 GitHub release 详情"""
import requests
TOKEN = open(r'E:\软件开发\云集智能视频创意站\dev\app\.github_token', encoding='utf-8').read().strip()
r = requests.get(
    'https://api.github.com/repos/yunjii-cn/vi/releases/336685383',
    headers={'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/vnd.github+json', 'User-Agent': 'YunJii/1.0'},
    timeout=15,
)
data = r.json()
print('upload_url:', data.get('upload_url'))
print('tag:', data.get('tag_name'))
print('assets:')
for a in data.get('assets', []):
    name = a.get('name')
    state = a.get('state')
    url = a.get('browser_download_url')
    print(f'  - {name}  state={state}  url={url}')
