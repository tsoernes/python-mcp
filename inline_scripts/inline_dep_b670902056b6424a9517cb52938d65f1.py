import time
import httpx
from pydantic import BaseModel

class Foo(BaseModel):
    x: int

print('httpx version:', httpx.__version__)
print('Model:', Foo(x=42))
for i in range(3):
    print('tick', i)
    time.sleep(0.5)