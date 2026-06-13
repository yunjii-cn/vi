"""调试 5:对比函数调用和内联调用的 prepared request"""
import sys
sys.path.insert(0, r'E:\软件开发\云集智能视频创意站\dev\app')
import requests
from requests import Request
from _publish_releases import _gh_headers

TOKEN = open(r'E:\软件开发\云集智能视频创意站\dev\app\.github_token', encoding='utf-8').read().strip()
exe_path = r'E:\软件开发\云集智能视频创意站\dev\ver\云集智能视频创意站-v2026.06.09.2220.exe'
fname = '云集智能视频创意站-v2026.06.09.2220.exe'

# === 模拟函数内的 URL 生成 ===
upload_url_template = 'https://uploads.github.com/repos/yunjii-cn/vi/releases/336691748/assets{?name,label}'
sep = '&' if '?' in upload_url_template else '?'
upload_url = f"{upload_url_template.split('{?name,label}')[0]}{sep}name={requests.utils.quote(fname)}"
print('URL:', upload_url)
print('URL == inline URL:', upload_url == 'https://uploads.github.com/repos/yunjii-cn/vi/releases/336691748/assets?name=%E4%BA%91%E9%9B%86%E6%99%BA%E8%83%BD%E8%A7%86%E9%A2%91%E5%88%9B%E6%84%8F%E7%AB%99-v2026.06.09.2220.exe')

with open(exe_path, 'rb') as f:
    raw = f.read()

# 用函数返回的 headers
hdrs = _gh_headers(TOKEN)
print('Headers from _gh_headers:')
for k, v in hdrs.items():
    print(f'  {k}: {v}')

# Prepared request
req = Request(
    method='POST',
    url=upload_url,
    headers={**hdrs, 'Content-Type': 'application/octet-stream'},
    data=raw,
)
prepared = req.prepare()
print('\nPrepared request headers:')
for k, v in prepared.headers.items():
    print(f'  {k}: {str(v)[:80]}')
print('Body length:', len(prepared.body) if prepared.body else 0)
print('Body starts with:', prepared.body[:30] if prepared.body else None)
