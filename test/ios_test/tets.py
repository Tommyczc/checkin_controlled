import asyncio
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.wda import WdaServiceClient

async def launch_app(bundle_id: str):
    async with await create_using_usbmux() as lockdown:
        wda_client = WdaServiceClient(lockdown)
        session_id = await wda_client.start_session(bundle_id=bundle_id)
        print(f"✅ 应用已启动，会话ID: {session_id}")

asyncio.run(launch_app("com.facebook.WebDriverAgentRunner.xctrunner.BR4JH5QHF7"))