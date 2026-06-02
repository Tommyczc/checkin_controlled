import asyncio

import cv2
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from controller.config_controller import config
from controller.devices_controller import devices

router = APIRouter(
    prefix="/mirror",
    tags=["mirror"],           # 在文档中分组
)

_mirror_locks: dict[str, asyncio.Lock] = {}


def _get_device_lock(device_id: str) -> asyncio.Lock:
    lock = _mirror_locks.get(device_id)
    if lock is None:
        lock = asyncio.Lock()
        _mirror_locks[device_id] = lock
    return lock


def _next_frame(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None


@router.websocket("/{device_id}")
async def mirror_websocket(websocket: WebSocket, device_id: str):
    await websocket.accept()

    controller = devices.get_device(device_id, refresh=True)
    if controller is None:
        await websocket.send_json({"ok": False, "error": f"未找到设备: {device_id}"})
        await websocket.close(code=4404)
        return

    lock = _get_device_lock(device_id)
    if lock.locked():
        await websocket.send_json({"ok": False, "error": f"设备 {device_id} 已有镜像会话正在运行"})
        await websocket.close(code=4409)
        return

    async with lock:
        streamer = None
        try:
            streamer = controller.start_mirror(
                max_fps=config.get("android.mirror.max_fps"),
                bit_rate=config.get("android.mirror.bit_rate"),
                max_size=config.get("android.mirror.max_size"),
                video_codec=config.get("android.mirror.video_codec"),
            )

            await websocket.send_json(
                {
                    "ok": True,
                    "type": "mirror_started",
                    "device_id": device_id,
                    "device": controller.to_server_payload(),
                }
            )

            frame_iterator = streamer.iter_frames()

            while True:
                frame = await asyncio.to_thread(_next_frame, frame_iterator)
                if frame is None:
                    await websocket.send_json(
                        {
                            "ok": False,
                            "type": "mirror_ended",
                            "device_id": device_id,
                            "error": "视频流已结束",
                        }
                    )
                    break

                success, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 80],
                )
                if not success:
                    continue

                await websocket.send_bytes(encoded.tobytes())
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            try:
                await websocket.send_json(
                    {
                        "ok": False,
                        "type": "mirror_error",
                        "device_id": device_id,
                        "error": str(exc),
                    }
                )
            except Exception:
                pass
        finally:
            if streamer is not None:
                controller.stop_mirror()
