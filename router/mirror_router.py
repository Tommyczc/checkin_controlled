import asyncio
import time

import cv2
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from controller.config_controller import config
from controller.devices_controller import devices
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()

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


def _format_resolution(video_size):
    if video_size is None:
        return None

    width, height = video_size
    return {
        "width": width,
        "height": height,
    }


def _get_int_config(name: str, default: int) -> int:
    value = config.get(name)
    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("镜像配置格式错误，使用默认值: %s=%s", name, value)
        return default


def _get_float_config(name: str, default: float) -> float:
    value = config.get(name)
    if value is None:
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("镜像配置格式错误，使用默认值: %s=%s", name, value)
        return default


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _resize_resolution(video_size: tuple[int, int], max_size: int) -> tuple[int, int]:
    width, height = video_size
    long_edge = max(width, height)
    if max_size <= 0 or long_edge <= max_size:
        return width, height

    scale = max_size / long_edge
    resized_width = max(1, int(round(width * scale)))
    resized_height = max(1, int(round(height * scale)))
    return resized_width, resized_height


def _resize_frame(frame, max_size: int):
    height, width = frame.shape[:2]
    resized_width, resized_height = _resize_resolution((width, height), max_size)
    if resized_width == width and resized_height == height:
        return frame

    interpolation = cv2.INTER_AREA if resized_width < width or resized_height < height else cv2.INTER_LINEAR
    return cv2.resize(frame, (resized_width, resized_height), interpolation=interpolation)


@router.get("/{device_id}/status")
async def get_mirror_status(device_id: str, refresh: bool = True):
    """查询镜像流状态和当前原始视频尺寸。"""
    logger.info("收到镜像状态请求: device_id=%s, refresh=%s", device_id, refresh)
    controller = devices.get_device(device_id, refresh=refresh)
    if controller is None:
        logger.warning("镜像状态请求失败，设备不存在: %s", device_id)
        raise HTTPException(status_code=404, detail=f"未找到设备: {device_id}")

    streamer = controller.screen_mirror
    raw_resolution = streamer.get_video_size()
    output_max_size = max(0, _get_int_config("android.mirror.output_max_size", 0))
    return {
        "device_id": device_id,
        "mirror_running": streamer.is_running(),
        "raw_resolution": _format_resolution(raw_resolution),
        "stream_resolution": _format_resolution(
            _resize_resolution(raw_resolution, output_max_size) if raw_resolution is not None else None
        ),
        "stream_options": {
            "jpeg_quality": _clamp_int(_get_int_config("android.mirror.jpeg_quality", 60), 20, 95),
            "output_max_fps": max(0.0, _get_float_config("android.mirror.output_max_fps", 20.0)),
            "output_max_size": output_max_size,
        },
    }


@router.websocket("/{device_id}")
async def mirror_websocket(websocket: WebSocket, device_id: str):
    await websocket.accept()
    logger.info("镜像 websocket 已连接: %s", device_id)

    controller = devices.get_device(device_id, refresh=True)
    if controller is None:
        logger.warning("镜像 websocket 连接失败，设备不存在: %s", device_id)
        await websocket.send_json({"ok": False, "error": f"未找到设备: {device_id}"})
        await websocket.close(code=4404)
        return

    lock = _get_device_lock(device_id)
    if lock.locked():
        logger.warning("镜像 websocket 连接被拒绝，已有会话占用: %s", device_id)
        await websocket.send_json({"ok": False, "error": f"设备 {device_id} 已有镜像会话正在运行"})
        await websocket.close(code=4409)
        return

    async with lock:
        streamer = None
        try:
            jpeg_quality = _clamp_int(_get_int_config("android.mirror.jpeg_quality", 60), 20, 95)
            output_max_fps = max(0.0, _get_float_config("android.mirror.output_max_fps", 20.0))
            output_max_size = max(0, _get_int_config("android.mirror.output_max_size", 0))
            send_interval = 1.0 / output_max_fps if output_max_fps > 0 else 0.0
            last_sent_at = 0.0

            logger.info("开始按需启动镜像流: %s", device_id)
            streamer = controller.start_mirror(
                max_fps=config.get("android.mirror.max_fps"),
                bit_rate=config.get("android.mirror.bit_rate"),
                max_size=config.get("android.mirror.max_size"),
                video_codec=config.get("android.mirror.video_codec"),
            )
            logger.info(
                "镜像 websocket 输出参数: device_id=%s, jpeg_quality=%s, output_max_fps=%s, output_max_size=%s",
                device_id,
                jpeg_quality,
                output_max_fps,
                output_max_size,
            )

            await websocket.send_json(
                {
                    "ok": True,
                    "type": "mirror_started",
                    "device_id": device_id,
                    "device": controller.to_server_payload(),
                    "stream_options": {
                        "jpeg_quality": jpeg_quality,
                        "output_max_fps": output_max_fps,
                        "output_max_size": output_max_size,
                    },
                }
            )

            frame_iterator = streamer.iter_frames()

            while True:
                frame = await asyncio.to_thread(_next_frame, frame_iterator)
                if frame is None:
                    logger.warning("镜像流已结束: %s", device_id)
                    await websocket.send_json(
                        {
                            "ok": False,
                            "type": "mirror_ended",
                            "device_id": device_id,
                            "error": "视频流已结束",
                        }
                    )
                    break

                now = time.monotonic()
                if send_interval > 0 and now - last_sent_at < send_interval:
                    continue

                frame = _resize_frame(frame, output_max_size)
                success, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality],
                )
                if not success:
                    continue

                await websocket.send_bytes(encoded.tobytes())
                last_sent_at = time.monotonic()
        except WebSocketDisconnect:
            logger.info("镜像 websocket 已断开: %s", device_id)
            pass
        except Exception as exc:
            logger.warning("镜像 websocket 处理失败: device_id=%s, error=%s", device_id, exc)
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
                logger.info("镜像 websocket 结束，停止镜像流: %s", device_id)
                controller.stop_mirror()
