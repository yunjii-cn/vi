import os, sys, logging, httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
import uvicorn

APP_NAME = '云集智能视频创意站'
VERSION = '2026.05.15.1327'

def _resolve_base_paths():
    if hasattr(sys, '_MEIPASS'):
        base = os.path.dirname(sys.executable)
        _ui_dir = os.path.join(base, "app", "resources", "ui")
        _outputs_dir = os.path.join(base, "data", "outputs")
        _log_dir = os.path.join(base, "temp", "logs")
        _icon_path = os.path.join(sys._MEIPASS, 'icon.png')
    else:
        _patches_dir = os.path.dirname(os.path.abspath(__file__))
        _res_dir = os.path.dirname(_patches_dir)
        _app_dir = os.path.dirname(_res_dir)
        _project_root = os.path.dirname(os.path.dirname(_app_dir))
        _ui_dir = os.path.join(_res_dir, "ui")
        _data_dir = os.path.join(_project_root, "data")
        _outputs_dir = os.path.join(_data_dir, "outputs")
        _temp_dir = os.path.join(_project_root, "temp")
        _log_dir = os.path.join(_temp_dir, "logs")
        _icon_path = os.path.join(_app_dir, 'icon.png')
    os.makedirs(_log_dir, exist_ok=True)
    os.makedirs(_outputs_dir, exist_ok=True)
    return _ui_dir, _outputs_dir, _log_dir, _icon_path

ui_dir, OUTPUTS_DIR, _ui_log_path, ICON_PATH = _resolve_base_paths()

def _ui_log(msg):
    with open(_ui_log_path, 'a', encoding='utf-8') as f:
        f.write(f"[UI] {msg}\n")
    print(f"[UI_SERVER] {msg}", flush=True)

def _safe_file(path, media_type, headers=None):
    with open(path, "rb") as f:
        return Response(content=f.read(), media_type=media_type, headers=headers or {})

_ui_log(f"Starting UI server, backend port=6000")

BACKEND_PORT = 6000
FRONTEND_PORT = 7000
BACKEND_BASE = f"http://127.0.0.1:{BACKEND_PORT}"
app = FastAPI()
NC = {"Cache-Control": "no-store, max-age=0"}
_ui_log(f"Routes configured, ui_dir={ui_dir}, outputs_dir={OUTPUTS_DIR}")

@app.get("/")
async def index():
    html_path = os.path.join(ui_dir, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    icon_b64 = ''
    if os.path.exists(ICON_PATH):
        import base64
        with open(ICON_PATH, "rb") as _f:
            icon_b64 = base64.b64encode(_f.read()).decode("ascii")
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

@app.get("/app-icon.png")
async def app_icon():
    icon_candidates = [ICON_PATH]
    if hasattr(sys, '_MEIPASS'):
        icon_candidates.insert(0, os.path.join(sys._MEIPASS, 'icon.png'))
    for p in icon_candidates:
        if os.path.exists(p):
            return _safe_file(p, "image/png", NC)
    return Response(content=b"Not found", status_code=404)

@app.api_route("/outputs/{path:path}", methods=["GET", "HEAD"])
async def proxy_outputs(request: Request, path: str):
    file_path = os.path.join(OUTPUTS_DIR, path)
    if not os.path.exists(file_path) or os.path.isdir(file_path):
        return Response(content=b"Not found", status_code=404)
    import mimetypes as _mt
    import re as _re
    file_size = os.path.getsize(file_path)
    mime_type, _ = _mt.guess_type(file_path)
    if mime_type is None:
        mime_type = "application/octet-stream"
    headers = dict(request.headers)
    range_header = headers.get("range", "")
    if range_header.startswith("bytes="):
        match = _re.match(r"^bytes=(\d+)-(\d*)$", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
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
    with open(file_path, "rb") as _f:
        return Response(content=_f.read(), media_type=mime_type, headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)})

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_api(request: Request, path: str):
    query = str(request.query_params)
    url = f"{BACKEND_BASE}/api/{path}"
    if query:
        url = f"{url}?{query}"

    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)

    is_media_request = path.startswith("system/file") or path.startswith("system/video-thumbnail")
    is_direct_file = path.startswith("system/file")

    if is_direct_file:
        import mimetypes
        import re
        file_path = request.query_params.get("path", "")
        if file_path and os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            mime_type, _ = mimetypes.guess_type(file_path)
            if mime_type is None:
                mime_type = "application/octet-stream"

            range_header = headers.get("range", "")
            if range_header.startswith("bytes="):
                match = re.match(r"^bytes=(\d+)-(\d*)$", range_header)
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else file_size - 1
                    end = min(end, file_size - 1)
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

                    _ui_log(f"MEDIA file direct read: status=206, path={file_path}, range={range_header}")
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
            else:
                _ui_log(f"MEDIA file direct read: status=200, path={file_path}, size={file_size}")
                with open(file_path, "rb") as _f:
                    return Response(content=_f.read(), media_type=mime_type, headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)})

    timeout = httpx.Timeout(300.0) if is_media_request else httpx.Timeout(60.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
