import sys, os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
patch_dir = r"E:\软件开发\云集智能视频创意站\dev\app\resources\patches"
backend_dir = r"E:\软件开发\云集智能视频创意站\dev\app\resources\backend"
sys.path = [p for p in sys.path if p and os.path.normpath(p) != os.path.normpath(backend_dir)]
sys.path = [p for p in sys.path if p and p != "." and p != ""]
sys.path.insert(0, patch_dir)
sys.path.insert(1, backend_dir)
import asyncio
import sys
import uvicorn, traceback, faulthandler
from ltx2_server import app

import socket as _sock
import threading as _threading
_orig_socketpair = _sock.socketpair
def _sp_lan_ip():
    try:
        _s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        try:
            _s.connect(("8.8.8.8", 80))
            return _s.getsockname()[0]
        finally:
            _s.close()
    except Exception:
        return "127.0.0.1"
def _try_tcp_pair(host, timeout=1.5):
    _ls = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    try:
        _ls.bind((host, 0)); _ls.listen(1); _addr = _ls.getsockname()
        _cs = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        _err = [None]; _done = _threading.Event()
        def _connect():
            try:
                _cs.connect(_addr)
            except Exception as _e:
                _err[0] = _e
            finally:
                _done.set()
        _t = _threading.Thread(target=_connect, daemon=True); _t.start()
        _ls.settimeout(timeout)
        try:
            _ss, _ = _ls.accept()
        except OSError:
            return None
        _done.wait(timeout)
        _ls.close()
        if _err[0] is not None:
            return None
        _ss.setblocking(False); _cs.setblocking(False)
        return (_cs, _ss)
    except Exception:
        return None
    finally:
        try:
            _ls.close()
        except Exception:
            pass
def _try_udp_pair(host):
    _a = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
    _b = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
    try:
        _a.bind((host, 0)); _b.bind((host, 0))
        _a.connect(_b.getsockname()); _b.connect(_a.getsockname())
        _a.settimeout(2.0); _b.settimeout(2.0)
        _a.send(bytes([0])); _got = _b.recv(1)
        if _got != bytes([0]):
            return None
        _a.setblocking(False); _b.setblocking(False)
        return (_a, _b)
    except Exception:
        for _s in (_a, _b):
            try:
                _s.close()
            except Exception:
                pass
        return None
def _robust_self_pair():
    # TCP 先试(若代理被关掉则秒过), 再退回 UDP 环回(不经 TCP 代理)
    for _host in ("127.0.0.1", _sp_lan_ip()):
        if not _host:
            continue
        _p = _try_tcp_pair(_host)
        if _p:
            return _p
        _p = _try_udp_pair(_host)
        if _p:
            return _p
    raise RuntimeError("all self-pair strategies failed (TCP+UDP loopback/LAN blocked)")
def _patched_socketpair(family=_sock.AF_INET, type=_sock.SOCK_STREAM, proto=0):
    if type != _sock.SOCK_STREAM:
        return _orig_socketpair(family, type, proto)
    try:
        return _robust_self_pair()
    except Exception as _e:
        raise RuntimeError(
            "socketpair fallback failed: %r - TCP+UDP loopback/LAN connect blocked "
            "by proxy/VPN/AV. Add 127.0.0.1, ::1 and the LAN IP to the proxy/VPN "
            "bypass list, or exit the filtering software." % (_e,)
        )
_sock.socketpair = _patched_socketpair
try:
    import asyncio.windows_utils as _wutils
    _wutils.socketpair = _patched_socketpair
except Exception:
    pass
if sys.platform == "win32":
    def _selector_loop_factory(self):
        return asyncio.SelectorEventLoop
    uvicorn.Config.get_loop_factory = _selector_loop_factory
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("[LAUNCHER] patched socket.socketpair -> TCP+UDP self-pair; forcing SelectorEventLoop", flush=True)


if __name__ == '__main__':
    faulthandler.dump_traceback_later(60, file=sys.stderr)
    print("[LAUNCHER] reached uvicorn.run port=6000 routes=", len(getattr(app, 'routes', [])), flush=True)
    try:
        uvicorn.run(app, host="0.0.0.0", port=6000, log_level="info", access_log=False)
        print("[LAUNCHER] uvicorn.run returned normally", flush=True)
    except Exception as _e:
        print("[LAUNCHER-FATAL]", repr(_e), flush=True)
        traceback.print_exc()
        raise
