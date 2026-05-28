import cv2
from pyscrcpy import Client

def on_frame(client, frame):
    # 可以在这里对 frame (numpy 数组) 做图像识别
    cv2.imshow('Screen', frame)
    cv2.waitKey(1)

client = Client(max_fps=20, max_size=1024)
client.on_frame(on_frame)  # 设置帧回调函数
client.start(threaded=True)  # 启动 scrcpy 客户端

# 在循环中执行其他操作或控制
while True:
    if client.last_frame is not None:
        # 例如，执行一个点击操作
        client.control.tap(500, 1000)
        break