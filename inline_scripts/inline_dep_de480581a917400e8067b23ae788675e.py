import time, requests
print('Async deps start, requests=', requests.__version__)
for i in range(5):
    print('ASYNC DEPS', i)
    time.sleep(0.5)