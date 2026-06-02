from fastapi import APIRouter, HTTPException

from controller.devices_controller import devices
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()

router = APIRouter(
    prefix="/devices",
    tags=["device"],           # 在文档中分组
)


@router.get("")
@router.get("/get_devices")
async def list_devices(refresh: bool = True):
    """获取所有在线设备。"""
    logger.info("收到设备列表请求: refresh=%s", refresh)
    items = devices.get_payloads(refresh=refresh)
    return {"items": items, "count": len(items)}


@router.get("/refresh")
async def refresh_devices():
    """主动刷新设备缓存。"""
    logger.info("收到设备刷新请求")
    current_devices = devices.refresh_devices()
    return {
        "items": [controller.to_server_payload() for controller in current_devices.values()],
        "count": len(current_devices),
    }


@router.get("/{device_id}")
async def get_device_detail(device_id: str, refresh: bool = False):
    """获取单个设备详情。"""
    logger.info("收到设备详情请求: device_id=%s, refresh=%s", device_id, refresh)
    controller = devices.get_device(device_id, refresh=refresh)
    if controller is None:
        logger.warning("设备详情请求失败，设备不存在: %s", device_id)
        raise HTTPException(status_code=404, detail=f"未找到设备: {device_id}")

    return controller.to_server_payload()


@router.get("/{device_id}/remote/info")
async def get_remote_info(device_id: str):
    """获取设备的 uiautomator2 运行信息。"""
    logger.info("收到远程信息请求: device_id=%s", device_id)
    controller = devices.get_device(device_id, refresh=True)
    if controller is None:
        logger.warning("远程信息请求失败，设备不存在: %s", device_id)
        raise HTTPException(status_code=404, detail=f"未找到设备: {device_id}")

    remote = controller.connect_remote()
    return {
        "device_id": device_id,
        "info": remote.info(),
        "app_current": remote.app_current(),
    }
