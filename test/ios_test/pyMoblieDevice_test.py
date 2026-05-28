import asyncio
import time

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.services.springboard import SpringBoardServicesService
from pymobiledevice3.services.wda import WdaServiceClient, WdaClient


# 使用 async def 定义一个异步主函数
async def get_apps():
    # 1. 使用 await 等待异步函数 create_using_usbmux 完成
    #    你可以把 "你的设备UDID" 替换成你的设备标识，如果只有一台设备可以留空
    async with await create_using_usbmux(serial="00008150-000958C13439401C") as lockdown:
        print(f"成功连接设备，UDID: {lockdown.identifier}")

        # 2. 使用 async with 来管理 InstallationProxyService
        async with InstallationProxyService(lockdown=lockdown) as ip:
            # 3. 调用 get_apps() 前也需要 await
            apps = await ip.get_apps()
            print(f"获取到 {len(apps)} 个应用:")
            for app_id, app_info in apps.items():
                # 这里注意字典的键是 app_id，值是另一个包含详细信息的字典
                app_name = app_info.get('CFBundleDisplayName', 'Unknown')
                print(f"  - {app_name} ({app_id})")


async def launch_app():
    async with await create_using_usbmux(serial="00008150-000958C13439401C") as lockdown:
        # 实例化时也使用正确的类名
        async with SpringBoardServicesService(lockdown=lockdown) as sb:
            # 启动指定应用
            await sb.start_tracking_app('com.apple.Preferences')
            print("已打开设置应用")

            # 回到主屏幕
            # await sb.press_home_button()
            # print("已返回主屏幕")


# async def main():
#     async with await create_using_usbmux() as lockdown:
#         wda_client = WdaServiceClient(lockdown)
#         # ⭐ 增加等待时间，给 WDA 更多启动时间
#         await asyncio.sleep(3)
#
#         bundle_id = "com.facebook.WebDriverAgentRunner.Tommy20260527.xctrunner"
#         print(f"正在尝试启动 {bundle_id}...")
#         session_id = None
#         # ⭐ 添加重试逻辑
#         for attempt in range(1, 4):
#             try:
#                 session_id = await wda_client.start_session(bundle_id=bundle_id)
#                 print(f"✅ 启动成功！会话ID: {session_id}")
#                 break
#             except Exception as e:
#                 print(f"尝试 {attempt}/3 失败: {e}")
#                 if attempt < 3:
#                     await asyncio.sleep(2)  # 等待2秒后重试
#                 else:
#                     raise

# def main():
#     # WdaClient 假定 WDA 服务已运行在 http://127.0.0.1:8100
#     client = WdaClient()
#     # ⭐ 添加等待，确保服务就绪
#     time.sleep(3)
#
#     bundle_id = "com.apple.Preferences"
#     print(f"正在启动 {bundle_id}...")
#     session_id = client.start_session(bundle_id=bundle_id)
#     print(f"✅ 启动成功！会话ID: {session_id}")
#
# if __name__ == "__main__":
#     main()

# # 运行异步主函数
if __name__ == "__main__":
    asyncio.run(get_apps())
