import wda
import time

from utils.pack_tool import run_cmd

run_cmd("tidevice xctest -B com.facebook.WebDriverAgentRunner.xctrunner.BR4JH5QHF7","unable to connect to webdriver")
c = wda.USBClient(udid="00008150-000958C13439401C")


# 2. 获取设备信息和状态，验证连接
print(f"设备信息: {c.info}")
print(f"状态: {c.status()}")


# # 3. 基础屏幕操作
c.home()                    # 按下 Home 键回到桌面


# 4. 应用操作
# 启动一个应用，例如设置 App
c.session().app_activate('com.laiwang.DingTalk')
time.sleep(2)

c.home()