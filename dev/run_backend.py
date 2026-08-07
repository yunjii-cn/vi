import sys
import os

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# 基于脚本位置推导路径，支持项目迁移
_script_dir = os.path.dirname(os.path.abspath(__file__))
os.environ["LTX_APP_DATA_DIR"] = os.path.join(_script_dir, "data")

patch_dir = os.path.join(_script_dir, "app", "resources", "patches")
backend_dir = os.path.join(_script_dir, "app", "resources", "backend")
sys.path = [p for p in sys.path if p and os.path.normpath(p) != os.path.normpath(backend_dir)]
sys.path = [p for p in sys.path if p and p != "." and p != ""]
sys.path.insert(0, patch_dir)
sys.path.insert(1, backend_dir)

import uvicorn
from ltx2_server import app

uvicorn.run(app, host="0.0.0.0", port=6000, log_level="info", access_log=False)
