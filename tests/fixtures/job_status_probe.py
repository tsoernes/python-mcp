"""Short child process used to verify smart-async output and status handling."""

import time

print("started", flush=True)
time.sleep(0.15)
print("finished", flush=True)
