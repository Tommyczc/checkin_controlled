from fastapi import APIRouter, HTTPException

from controller.devices_controller import devices

router = APIRouter(
    prefix="/devices",
    tags=["device"],           # 在文档中分组
)


@router.get("")
@router.get("/get_devices")
async def list_devices(refresh: bool = True):
    """获取所有在线设备。"""
    items = devices.get_payloads(refresh=refresh)
    return {"items": items, "count": len(items)}


@router.get("/refresh")
async def refresh_devices():
    """主动刷新设备缓存。"""
    current_devices = devices.refresh_devices()
    return {
        "items": [controller.to_server_payload() for controller in current_devices.values()],
        "count": len(current_devices),
    }


@router.get("/{device_id}")
async def get_device_detail(device_id: str, refresh: bool = False):
    """获取单个设备详情。"""
    controller = devices.get_device(device_id, refresh=refresh)
    if controller is None:
        raise HTTPException(status_code=404, detail=f"未找到设备: {device_id}")

    return controller.to_server_payload()


@router.get("/{device_id}/remote/info")
async def get_remote_info(device_id: str):
    """获取设备的 uiautomator2 运行信息。"""
    controller = devices.get_device(device_id, refresh=True)
    if controller is None:
        raise HTTPException(status_code=404, detail=f"未找到设备: {device_id}")

    remote = controller.connect_remote()
    return {
        "device_id": device_id,
        "info": remote.info(),
        "app_current": remote.app_current(),
    }
