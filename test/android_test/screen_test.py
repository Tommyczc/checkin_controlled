import uiautomator2 as u2
import cv2
import time

# 1. 连接设备（默认连接通过 USB 连接的第一台设备）
print("正在连接手机...")
d = u2.connect()  # 也可以指定序列号: u2.connect('your_device_serial')
print("连接成功！")

# 2. 获取屏幕尺寸并打印，方便了解设备信息
info = d.info
print(f"设备信息: {info['productName']}, 分辨率: {info['displayWidth']}x{info['displayHeight']}")

print("开始屏幕镜像，按 'q' 键在控制台退出程序...")

try:
    while True:
        # 3. 获取实时截图，并直接转换为 OpenCV 格式的 numpy 数组
        #    这利用了 uiautomator2 集成的 minicap 技术，速度很快[reference:14]
        frame = d.screenshot(format='opencv')

        # 4. 如果成功获取到画面，就用 OpenCV 显示出来
        if frame is not None:
            # 在窗口中显示画面
            cv2.imshow('uiautomator2 Live Mirror', frame)
        else:
            print("警告: 截图失败")

        # 5. 等待1毫秒并检测按键，如果按下 'q' 键，则跳出循环
        #    waitKey(1) 函数是 OpenCV 显示画面的关键，它会处理窗口事件
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

        # 可选：控制帧率，避免屏幕刷新过快，这里设置为每秒20帧左右
        time.sleep(0.05)

except KeyboardInterrupt:
    print("用户中断程序。")
finally:
    # 6. 程序结束时关闭所有 OpenCV 窗口，释放资源
    print("正在关闭窗口...")
    cv2.destroyAllWindows()
    print("程序已退出。")