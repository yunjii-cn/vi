"""调试:尝试用 files= 上传"""
import requests
TOKEN = open(r'E:\软件开发\云集智能视频创意站\dev\app\.github_token', encoding='utf-8').read().strip()
exe_path = r'E:\软件开发\云集智能视频创意站\dev\ver\云集智能视频创意站-v2026.06.09.2220.exe'
fname = '云集智能视频创意站-v2026.06.09.2220.exe'

# 方法 1: data=bytes + Content-Type
print("=== 方法 1: data=bytes ===")
with open(exe_path, 'rb') as f:
    raw = f.read()
r = requests.post(
    'https://uploads.github.com/repos/yunjii-cn/vi/releases/336685383/assets?name=test1.bin',
    headers={
        'Authorization': f'Bearer {TOKEN}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'YunJii/1.0',
        'Content-Type': 'application/octet-stream',
    },
    data=raw,
    timeout=600,
)
print(f'  Status: {r.status_code}')
print(f'  Response: {r.text[:300]}')
