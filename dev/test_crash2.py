import sys
import os

sys.path.insert(0, r'E:\软件开发\云集智能视频创意站\dev\app')
os.chdir(r'E:\软件开发\云集智能视频创意站\dev\app')

import faulthandler
faulthandler.enable()

import main

print("=== Starting main.main() ===", flush=True)
try:
    main.main()
except Exception as e:
    print(f"Exception: {e}", flush=True)
    import traceback
    traceback.print_exc()
