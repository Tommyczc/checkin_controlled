import os
import asyncio
from android_controller import Controller  # 导入核心类

# 需要先准备好你的 adb 和 scrcpy 程序
# 如果已经将它们所在目录添加到了系统的 PATH 环境变量，直接写文件名也行；否则需要写完整的路径
ADB_PATH = "adb"  # 或者 "D:/platform-tools/adb.exe"
SCRCPY_PATH = "scrcpy"  # 或者 "D:/scrcpy/scrcpy.exe"


async def main():
    # 1. 初始化控制器
    ctrl = Controller(adb_path=ADB_PATH, scrcpy_path=SCRCPY_PATH)

    # 2. 获取并打印设备信息，这是确认连接最简单的方法
    device_info = ctrl.getDevice()
    print(f"已连接设备: {device_info}")

    # 3. 进行一个简单的操作验证：点击坐标 (500, 1000)
    print("正在尝试点击坐标 (500, 1000)...")
    ctrl.tap((500, 1000))

    # 4. 开始屏幕镜像 (异步操作，会持续运行)
    # 按 Ctrl+C 可以停止镜像
    await ctrl.stream(max_fps=30)


if __name__ == "__main__":
    asyncio.run(main())

