import asyncio
import time

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


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _get_h264_chunk_size() -> int:
    return _clamp_int(_get_int_config("android.mirror.h264_chunk_size", 32768), 4096, 262144)


@router.get("/{device_id}/status")
async def get_mirror_status(device_id: str, refresh: bool = True):
    """查询镜像流状态和当前原始视频尺寸。"""
    logger.info("收到镜像状态请求: device_id=%s, refresh=%s", device_id, refresh)
    controller = devices.get_device(device_id, refresh=refresh)
    if controller is None:
        logger.warning("镜像状态请求失败，设备不存在: %s", device_id)
        raise HTTPException(status_code=404, detail=f"未找到设备: {device_id}")

    streamer = controller.screen_mirror
    raw_resolution = streamer.get_or_detect_video_size()
    logger.info("raw_resolution: %s", raw_resolution)
    return {
        "device_id": device_id,
        "mirror_running": streamer.is_running(),
        "raw_resolution": _format_resolution(raw_resolution),
        "stream_resolution": _format_resolution(raw_resolution),
        "stream_options": {
            "stream_format": "h264",
            "video_codec": config.get("android.mirror.video_codec"),
            "max_fps": _get_int_config("android.mirror.max_fps", 30),
            "bit_rate": _get_int_config("android.mirror.bit_rate", 4_000_000),
            "max_size": _get_int_config("android.mirror.max_size", 0),
            "h264_chunk_size": _get_h264_chunk_size(),
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
            max_fps = _get_int_config("android.mirror.max_fps", 30)
            bit_rate = _get_int_config("android.mirror.bit_rate", 4_000_000)
            max_size = _get_int_config("android.mirror.max_size", 0)
            video_codec = config.get("android.mirror.video_codec") or "h264"
            chunk_size = _get_h264_chunk_size()

            logger.info("开始按需启动镜像流: %s", device_id)
            streamer = controller.start_mirror(
                max_fps=max_fps,
                bit_rate=bit_rate,
                max_size=max_size,
                video_codec=video_codec,
            )
            logger.info(
                "镜像 websocket 输出参数: device_id=%s, stream_format=h264, video_codec=%s, max_fps=%s, bit_rate=%s, max_size=%s, chunk_size=%s",
                device_id,
                video_codec,
                max_fps,
                bit_rate,
                max_size,
                chunk_size,
            )
            raw_resolution = streamer.get_or_detect_video_size()

            await websocket.send_json(
                {
                    "ok": True,
                    "type": "mirror_started",
                    "device_id": device_id,
                    "device": controller.to_server_payload(),
                    "raw_resolution": _format_resolution(raw_resolution),
                    "stream_resolution": _format_resolution(raw_resolution),
                    "stream_options": {
                        "stream_format": "h264",
                        "video_codec": video_codec,
                        "max_fps": max_fps,
                        "bit_rate": bit_rate,
                        "max_size": max_size,
                        "h264_chunk_size": chunk_size,
                    },
                }
            )

            sent_chunks = 0
            sent_bytes = 0
            last_stats_at = time.monotonic()

            while True:
                chunk = await asyncio.to_thread(streamer.read_video_chunk, chunk_size)
                if not chunk:
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

                send_started_at = time.monotonic()
                await websocket.send_bytes(chunk)
                sent_at = time.monotonic()
                sent_chunks += 1
                sent_bytes += len(chunk)

                if sent_at - last_stats_at >= 5.0:
                    logger.info(
                        "镜像 H.264 websocket 性能: device_id=%s, chunks=%s, mb=%.2f, send_ms=%.1f",
                        device_id,
                        sent_chunks,
                        sent_bytes / 1024 / 1024,
                        (sent_at - send_started_at) * 1000,
                    )
                    sent_chunks = 0
                    sent_bytes = 0
                    last_stats_at = sent_at
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
