import os, sys, logging, httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
import uvicorn

APP_NAME = '云集智能视频创意站'
VERSION = '2026.06.09.0523'

_ui_log_path = 'E:\\软件开发\\云集智能视频创意站\\dev\\temp\\logs\\ui_server.log'
os.makedirs(os.path.dirname(_ui_log_path), exist_ok=True)

def _ui_log(msg):
    with open(_ui_log_path, 'a', encoding='utf-8') as f:
        f.write(f"[UI] {msg}\n")
    print(f"[UI_SERVER] {msg}", flush=True)

def _safe_file(path, media_type, headers=None):
    with open(path, "rb") as f:
        return Response(content=f.read(), media_type=media_type, headers=headers or {})

_ui_log(f"Starting UI server, backend port=6000")

ui_dir = 'E:\\软件开发\\云集智能视频创意站\\dev\\app\\resources\\ui'
BACKEND_PORT = 6000
FRONTEND_PORT = 7000
BACKEND_BASE = f"http://127.0.0.1:{BACKEND_PORT}"
app = FastAPI()
NC = {"Cache-Control": "no-store, max-age=0"}
_ui_log(f"Routes configured, ui_dir={ui_dir}")

@app.get("/")
async def index():
    html_path = os.path.join(ui_dir, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    icon_b64 = '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'
    if icon_b64:
        html = html.replace('src="/app-icon.png"', f'src="data:image/png;base64,{icon_b64}"')
    return Response(content=html.encode("utf-8"), media_type="text/html", headers=NC)

@app.get("/api/app-info")
async def app_info():
    return {"app_name": APP_NAME, "version": VERSION}

@app.get("/index.css")
async def css():
    return _safe_file(os.path.join(ui_dir, "index.css"), "text/css", NC)

@app.get("/index.js")
async def js():
    with open(os.path.join(ui_dir, "index.js"), "r", encoding="utf-8") as f:
        content = f.read()
    content = content.replace("{{BACKEND_PORT}}", str(BACKEND_PORT))
    return Response(content=content, media_type="application/javascript", headers=NC)

@app.get("/i18n.js")
async def i18n():
    return _safe_file(os.path.join(ui_dir, "i18n.js"), "application/javascript", NC)

@app.get("/docs")
async def usage_guide():
    guide_path = os.path.join(ui_dir, "usage_guide.html")
    return _safe_file(guide_path, "text/html; charset=utf-8", NC)

@app.get("/app-icon.png")
async def app_icon():
    icon_candidates = ['E:\\软件开发\\云集智能视频创意站\\dev\\app\\ico.png']
    if hasattr(sys, '_MEIPASS'):
        for name in ('ico.png', 'icon.png', 'icon.ico'):
            icon_candidates.insert(0, os.path.join(sys._MEIPASS, name))
    for p in icon_candidates:
        if os.path.exists(p):
            return _safe_file(p, "image/png", NC)
    return Response(content=b"Not found", status_code=404)

@app.api_route("/outputs/{path:path}", methods=["GET", "HEAD"])
async def proxy_outputs(request: Request, path: str):
    outputs_dir = 'E:\\软件开发\\云集智能视频创意站\\dev\\data\\outputs'
    file_path = os.path.join(outputs_dir, path)
    if not os.path.exists(file_path) or os.path.isdir(file_path):
        return Response(content=b"Not found", status_code=404)
    import mimetypes as _mt
    import re as _re
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        return Response(content=b"Internal error", status_code=500)
    mime_type, _ = _mt.guess_type(file_path)
    if mime_type is None:
        mime_type = "application/octet-stream"
    base_headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size), "Content-Type": mime_type}
    if request.method == "HEAD":
        return Response(content=b"", status_code=200, media_type=mime_type, headers=base_headers)
    headers = dict(request.headers)
    range_header = headers.get("range", "")
    if range_header.startswith("bytes="):
        match = _re.match(r"^bytes=(\d+)-(\d*)$", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            if start > end or start >= file_size:
                return Response(content=b"Invalid range", status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
            content_length = end - start + 1
            def _iter():
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            return StreamingResponse(
                _iter(),
                status_code=206,
                media_type=mime_type,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(content_length),
                },
            )
    def _full_iter():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk
    return StreamingResponse(_full_iter(), status_code=200, media_type=mime_type, headers=base_headers)

@app.api_route("/api/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_api(request: Request, path: str):
    query = str(request.query_params)
    url = f"{BACKEND_BASE}/api/{path}"
    if query:
        url = f"{url}?{query}"
    
    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    
    is_upload_request = path.startswith("system/upload")
    is_media_request = path.startswith("system/file") or path.startswith("system/video-thumbnail") or is_upload_request
    is_direct_file = path.startswith("system/file")
    
    if is_direct_file:
        import mimetypes
        import re
        file_path = request.query_params.get("path", "")
        if not file_path or not os.path.exists(file_path):
            if request.method == "HEAD":
                return Response(content=b"", status_code=404, media_type="application/octet-stream")
            return Response(content=b"Not found", status_code=404)
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            return Response(content=b"Internal error", status_code=500)
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            mime_type = "application/octet-stream"
        base_headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size), "Content-Type": mime_type}
        if request.method == "HEAD":
            return Response(content=b"", status_code=200, media_type=mime_type, headers=base_headers)
        range_header = headers.get("range", "")
        if range_header.startswith("bytes="):
            match = re.match(r"^bytes=(\d+)-(\d*)$", range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                end = min(end, file_size - 1)
                if start > end or start >= file_size:
                    return Response(content=b"Invalid range", status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
                content_length = end - start + 1
                
                def iterfile():
                    with open(file_path, "rb") as f:
                        f.seek(start)
                        remaining = content_length
                        while remaining > 0:
                            chunk = f.read(min(65536, remaining))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            yield chunk
                
                return StreamingResponse(
                    iterfile(),
                    status_code=206,
                    media_type=mime_type,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(content_length),
                    },
                )
        def _full_iter():
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        return StreamingResponse(_full_iter(), status_code=200, media_type=mime_type, headers=base_headers)
    
    timeout = httpx.Timeout(300.0) if is_media_request else httpx.Timeout(60.0)
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if is_upload_request:
                # Stream large uploads directly without buffering entire body
                async with client.stream(request.method, url, content=body, headers=headers) as resp:
                    resp_content = b""
                    async for chunk in resp.aiter_bytes():
                        resp_content += chunk
                    return Response(
                        content=resp_content,
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"),
                    )
            else:
                resp = await client.request(request.method, url, content=body, headers=headers)
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"),
                )
    except httpx.ConnectError:
        return Response(content=b'{"detail":"Backend unavailable","status":"offline"}', status_code=503, media_type="application/json")
    except httpx.TimeoutException:
        return Response(content=b'{"detail":"Backend timeout","status":"timeout"}', status_code=504, media_type="application/json")
    except Exception as e:
        _ui_log(f"PROXY ERROR: {e}")
        return Response(content=str(e).encode(), status_code=502)

@app.api_route("/health", methods=["GET"])
async def proxy_health():
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(f"{BACKEND_BASE}/health")
            return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
    except (httpx.ConnectError, httpx.TimeoutException):
        return Response(content=b'{"status":"offline","models_loaded":false}', status_code=503, media_type="application/json")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.platform == 'win32':
        class NF(logging.Filter):
            def filter(self, r):
                if r.name != "asyncio": return True
                m = r.getMessage()
                if "_call_connection_lost" in m or "_ProactorBasePipeTransport" in m: return False
                if hasattr(r, 'exc_info') and r.exc_info:
                    _, e, _ = r.exc_info
                    if isinstance(e, ConnectionResetError) and getattr(e, 'winerror', None) == 10054: return False
                if "10054" in m and "ConnectionResetError" in m: return False
                return True
        logging.getLogger("asyncio").addFilter(NF())
    _ui_log(f"Starting uvicorn on port {FRONTEND_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=FRONTEND_PORT, log_level="info", access_log=False)
