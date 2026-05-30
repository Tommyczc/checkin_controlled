import cv2
from adbnativeblitz import AdbNativeBlitz

with AdbNativeBlitz(device_serial="你的设备序列号") as shosho:
    for frame in shosho:
        cv2.imshow('Screen', frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()