"""调试 4:用同样的 URL 模式重试"""
import requests
from requests import Request
TOKEN = open(r'E:\软件开发\云集智能视频创意站\dev\app\.github_token', encoding='utf-8').read().strip()
exe_path = r'E:\软件开发\云集智能视频创意站\dev\ver\云集智能视频创意站-v2026.06.09.2220.exe'

# 用 release 336691748(刚刚创建的空的)
release_id = "336691748"
fname = '云集智能视频创意站-v2026.06.09.2220.exe'
url = f'https://uploads.github.com/repos/yunjii-cn/vi/releases/{release_id}/assets?name={requests.utils.quote(fname)}'
print('URL:', url)

with open(exe_path, 'rb') as f:
    raw = f.read()

req = Request(
    method='POST',
    url=url,
    headers={
        'Authorization': f'Bearer {TOKEN}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'YunJii/1.0',
        'Content-Type': 'application/octet-stream',
    },
    data=raw,
)
prepared = req.prepare()
print('  Content-Type:', prepared.headers.get('Content-Type'))
print('  Content-Length:', prepared.headers.get('Content-Length'))
r = requests.Session().send(prepared, timeout=600)
print(f'  Status: {r.status_code}')
print(f'  Response: {r.text[:200]}')
