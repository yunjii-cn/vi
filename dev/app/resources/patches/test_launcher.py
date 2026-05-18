import sys, os
patch_dir = r"E:\软件开发\云集智能视频创意站\dev\app\resources\patches"
backend_dir = r"E:\软件开发\云集智能视频创意站\dev\app\resources\backend"
sys.path = [p for p in sys.path if p and os.path.normpath(p) != os.path.normpath(backend_dir)]
sys.path = [p for p in sys.path if p and p != "." and p != ""]
sys.path.insert(0, patch_dir)
sys.path.insert(1, backend_dir)
import uvicorn
from ltx2_server import app
if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=3000, log_level="info", access_log=False)
