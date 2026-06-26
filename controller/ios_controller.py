from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from controller.ios_mirror_controller import IosMirrorStreamer
from controller.ios_remote_operation_controller import IosRemoteOperationController
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()


@dataclass
class IosDeviceInfo:
    """单台 iOS 设备的基础信息。"""

    device_id: str
    udid: str
    name: str = ""
    product_type: str = ""
    ios_version: str = ""
    connection_type: str = ""
    state: str = "device"
    platform: str = "ios"
    local_wda_port: int = 8100
    local_mjpeg_port: int = 9100
    local_replaykit_port: int = 27777
    device_wda_port: int = 8100
    device_mjpeg_port: int = 9100
    device_replaykit_port: int = 27777

    def display_name(self) -> str:
        parts = [part for part in [self.name, self.product_type] if part]
        return " ".join(parts) if parts else self.udid

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["display_name"] = self.display_name()
        return data


class IosController:
    """单台 iOS 设备控制器，聚合远程操作与屏幕镜像。"""

    def __init__(
        self,
        device_info: IosDeviceInfo,
        remote_operation: Optional[IosRemoteOperationController] = None,
        screen_mirror: Optional[IosMirrorStreamer] = None,
    ):
        self.device_info = device_info
        self.remote_operation = remote_operation or IosRemoteOperationController(
            device_id=device_info.device_id,
            udid=device_info.udid,
            device_wda_port=device_info.device_wda_port,
            local_wda_port=device_info.local_wda_port,
            auto_connect=False,
        )
        self.screen_mirror = screen_mirror or IosMirrorStreamer(
            device_id=device_info.device_id,
            udid=device_info.udid,
            device_name=device_info.name,
            device_wda_port=device_info.device_wda_port,
            device_mjpeg_port=device_info.device_mjpeg_port,
            device_replaykit_port=device_info.device_replaykit_port,
            local_wda_port=device_info.local_wda_port,
            local_mjpeg_port=device_info.local_mjpeg_port,
            local_replaykit_port=device_info.local_replaykit_port,
        )
        logger.info("单台 iOS 设备控制器初始化完成: %s", device_info.to_dict())

    @property
    def device_id(self) -> str:
        return self.device_info.device_id

    def update_device_info(self, device_info: IosDeviceInfo) -> None:
        logger.info("更新 iOS 设备信息: %s", device_info.to_dict())
        self.device_info = device_info
        self.remote_operation.local_wda_port = device_info.local_wda_port
        self.remote_operation.device_wda_port = device_info.device_wda_port
        self.remote_operation.wda_port = device_info.local_wda_port
        self.screen_mirror.device_name = device_info.name
        self.screen_mirror.local_wda_port = device_info.local_wda_port
        self.screen_mirror.local_mjpeg_port = device_info.local_mjpeg_port
        self.screen_mirror.local_replaykit_port = device_info.local_replaykit_port
        self.screen_mirror.device_wda_port = device_info.device_wda_port
        self.screen_mirror.device_mjpeg_port = device_info.device_mjpeg_port
        self.screen_mirror.device_replaykit_port = device_info.device_replaykit_port
        self.screen_mirror.wda_port = device_info.local_wda_port
        self.screen_mirror.mjpeg_port = device_info.local_mjpeg_port
        self.screen_mirror.replaykit_port = device_info.local_replaykit_port

    def connect_remote(self, force_reconnect: bool = False) -> IosRemoteOperationController:
        logger.info("开始连接 iOS 远程操作控制器: device_id=%s, force_reconnect=%s", self.device_id, force_reconnect)
        self.remote_operation.connect(force_reconnect=force_reconnect)
        logger.info("iOS 远程操作控制器连接成功: %s", self.device_id)
        return self.remote_operation

    def start_mirror(
        self,
        max_fps: Optional[int] = None,
        bit_rate: Optional[int] = None,
        max_size: Optional[int] = None,
        video_codec: Optional[str] = None,
    ) -> IosMirrorStreamer:
        logger.info(
            "开始启动 iOS 镜像流: device_id=%s, max_fps=%s, bit_rate=%s, max_size=%s, video_codec=%s",
            self.device_id,
            max_fps if max_fps is not None else self.screen_mirror.max_fps,
            bit_rate if bit_rate is not None else self.screen_mirror.bit_rate,
            max_size if max_size is not None else self.screen_mirror.max_size,
            video_codec if video_codec is not None else self.screen_mirror.video_codec,
        )
        if max_fps is not None:
            self.screen_mirror.max_fps = max_fps
        if bit_rate is not None:
            self.screen_mirror.bit_rate = bit_rate
        if max_size is not None:
            self.screen_mirror.max_size = max_size
        if video_codec is not None:
            self.screen_mirror.video_codec = video_codec

        mirror_requires_wda = self.screen_mirror.requires_wda_for_mirror()
        if mirror_requires_wda:
            try:
                self.connect_remote()
            except Exception as exc:
                logger.warning("iOS 镜像启动前 WDA 控制状态检查失败，继续尝试启动镜像流: device_id=%s, error=%s", self.device_id, exc)

        self.screen_mirror.start()
        if mirror_requires_wda:
            try:
                self.connect_remote(force_reconnect=True)
            except Exception as exc:
                logger.warning("iOS 镜像启动后 WDA 控制重连失败，保留已有连接: device_id=%s, error=%s", self.device_id, exc)
        logger.info("iOS 镜像流启动成功: %s", self.device_id)
        return self.screen_mirror

    def stop_mirror(self) -> None:
        logger.info("开始停止 iOS 镜像流: %s", self.device_id)
        self.screen_mirror.stop()
        logger.info("iOS 镜像流已停止: %s", self.device_id)

    def snapshot(self, format: str = "pillow") -> Any:
        self.connect_remote()
        return self.remote_operation.screenshot(format=format)

    def current_app(self) -> dict[str, Any]:
        self.connect_remote()
        return self.remote_operation.app_current()

    def to_server_payload(self) -> dict[str, Any]:
        payload = self.device_info.to_dict()
        payload["remote_connected"] = self.remote_operation.is_connected()
        payload["mirror_running"] = self.screen_mirror.is_running()
        return payload

    def close(self) -> None:
        logger.info("开始关闭单台 iOS 设备控制器: %s", self.device_id)
        try:
            self.stop_mirror()
        except Exception as exc:
            logger.warning("停止 iOS 镜像流失败: device_id=%s, error=%s", self.device_id, exc)
        self.remote_operation.disconnect()
        logger.info("单台 iOS 设备控制器已关闭: %s", self.device_id)
