from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from controller.devices_controller import devices
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()

router = APIRouter(
    prefix="/operation",
    tags=["operation"],           # 在文档中分组
)


def _execute_action(remote, action: str, payload: dict):
    if action == "ping":
        return {"pong": True}
    if action == "screen_on":
        remote.screen_on()
        return {"done": True}
    if action == "screen_off":
        remote.screen_off()
        return {"done": True}
    if action == "unlock":
        remote.unlock()
        return {"done": True}
    if action == "click":
        remote.click(int(payload["x"]), int(payload["y"]))
        return {"done": True}
    if action == "long_click":
        remote.long_click(
            int(payload["x"]),
            int(payload["y"]),
            float(payload.get("duration", 0.5)),
        )
        return {"done": True}
    if action == "swipe":
        remote.swipe(
            int(payload["fx"]),
            int(payload["fy"]),
            int(payload["tx"]),
            int(payload["ty"]),
            float(payload.get("duration", 0.1)),
        )
        return {"done": True}
    if action == "press":
        remote.press(payload["key"])
        return {"done": True}
    if action == "set_text":
        remote.set_text(
            text=str(payload.get("text", "")),
            clear=bool(payload.get("clear", True)),
        )
        return {"done": True}
    if action == "start_app":
        remote.start_app(
            package_name=str(payload["package_name"]),
            stop=bool(payload.get("stop", False)),
        )
        return {"done": True}
    if action == "stop_app":
        remote.stop_app(str(payload["package_name"]))
        return {"done": True}
    if action == "app_current":
        return remote.app_current()
    if action == "info":
        return remote.info()
    if action == "dump_hierarchy":
        return {
            "xml": remote.dump_hierarchy(
                compressed=bool(payload.get("compressed", False)),
                pretty=bool(payload.get("pretty", False)),
            )
        }

    raise ValueError(f"不支持的操作: {action}")


@router.websocket("/{device_id}")
async def operation_websocket(websocket: WebSocket, device_id: str):
    await websocket.accept()
    logger.info("远程控制 websocket 已连接: %s", device_id)

    controller = devices.get_device(device_id, refresh=True)
    if controller is None:
        logger.warning("远程控制 websocket 连接失败，设备不存在: %s", device_id)
        await websocket.send_json({"ok": False, "error": f"未找到设备: {device_id}"})
        await websocket.close(code=4404)
        return

    try:
        remote = controller.connect_remote()
        await websocket.send_json(
            {
                "ok": True,
                "type": "connected",
                "device_id": device_id,
                "device": controller.to_server_payload(),
            }
        )

        while True:
            payload = await websocket.receive_json()
            action = str(payload.get("action", "")).strip()
            if not action:
                logger.warning("远程控制 websocket 收到空 action: %s", device_id)
                await websocket.send_json({"ok": False, "error": "缺少 action 字段"})
                continue

            try:
                logger.info("执行远程控制动作: device_id=%s, action=%s, payload=%s", device_id, action, _action_payload_summary(payload))
                result = _execute_action(remote, action, payload)
                await websocket.send_json(
                    {
                        "ok": True,
                        "action": action,
                        "device_id": device_id,
                        "result": result,
                    }
                )
            except Exception as exc:
                logger.warning(
                    "远程控制动作执行失败: device_id=%s, action=%s, payload=%s, error=%s",
                    device_id,
                    action,
                    _action_payload_summary(payload),
                    exc,
                )
                await websocket.send_json(
                    {
                        "ok": False,
                        "action": action,
                        "device_id": device_id,
                        "error": str(exc),
                    }
                )
    except WebSocketDisconnect:
        logger.info("远程控制 websocket 已断开: %s", device_id)
        return


def _action_payload_summary(payload: dict) -> dict:
    keys = (
        "x",
        "y",
        "fx",
        "fy",
        "tx",
        "ty",
        "duration",
        "key",
        "package_name",
        "clear",
    )
    return {key: payload[key] for key in keys if key in payload}
