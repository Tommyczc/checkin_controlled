import cv2
import numpy as np
from adbblitz import AdbShotUSB

# 1. 通过 TCP 连接到设备（适用于有线和无线）
#    device_serial 可以替换为你设备的 IP:端口 (如 "192.168.1.100:5555")
#    或者先通过 adb devices 找到设备序列号，如 "emulator-5554"
with AdbShotUSB(device_serial="10CF1E2C5S0023A") as shosho:
    for frame in shosho:
        # 2. 检查并跳过无效帧
        if frame.dtype == np.uint16:
            continue
        # 3. 显示画面
        cv2.imshow("android screen", frame)
        # 4. 等待 1 毫秒，检测按键
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()