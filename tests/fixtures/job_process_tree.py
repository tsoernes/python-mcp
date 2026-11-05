"""Start a child process and record both PIDs for cancellation tests."""

import os
import subprocess
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
_ = marker.write_text(f"{os.getpid()} {child.pid}", encoding="utf-8")
print("process tree ready", flush=True)
while True:
    time.sleep(1)
