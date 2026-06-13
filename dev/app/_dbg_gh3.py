"""调试 2:对比 data=bytes vs files= vs 显式 Content-Type"""
import requests
TOKEN = open(r'E:\软件开发\云集智能视频创意站\dev\app\.github_token', encoding='utf-8').read().strip()
exe_path = r'E:\软件开发\云集智能视频创意站\dev\ver\云集智能视频创意站-v2026.06.09.2220.exe'

# 方法 2: data=bytes + 显式 Content-Type(应该和之前一样)
print("=== 方法 A: data=bytes + Content-Type,使用 prepared request ===")
with open(exe_path, 'rb') as f:
    raw = f.read()

from requests import Request
req = Request(
    method='POST',
    url='https://uploads.github.com/repos/yunjii-cn/vi/releases/336687342/assets?name=test_a.bin',
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
print('  Prepared Content-Type:', prepared.headers.get('Content-Type'))
print('  Prepared Content-Length:', prepared.headers.get('Content-Length'))
# 看看 body
print('  Body starts with:', prepared.body[:50] if prepared.body else None)
# 实际发送
s = requests.Session()
r = s.send(prepared, timeout=600)
print(f'  Status: {r.status_code}')
print(f'  Response: {r.text[:200]}')
