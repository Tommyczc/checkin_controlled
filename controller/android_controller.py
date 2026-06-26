from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from controller.android_remote_operation_controller import AndroidRemoteOperationController
from controller.android_screen_mirror_controller import AndroidScrcpyStreamer
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()

@dataclass
class AndroidDeviceInfo:
    """单台安卓设备的基础信息。"""

    device_id: str
    brand: str = ""
    model: str = ""
    manufacturer: str = ""
    android_version: str = ""
    sdk_version: str = ""
    state: str = "device"

    def display_name(self) -> str:
        parts = [part for part in [self.brand, self.model] if part]
        return " ".join(parts) if parts else self.device_id

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["display_name"] = self.display_name()
        return data


class AndroidController:
    """单台安卓设备控制器，聚合远程操作与屏幕镜像。"""

    def __init__(
        self,
        device_info: AndroidDeviceInfo,
        remote_operation: Optional[AndroidRemoteOperationController] = None,
        screen_mirror: Optional[AndroidScrcpyStreamer] = None,
    ):
        self.device_info = device_info
        self.remote_operation = remote_operation or AndroidRemoteOperationController(
            device_id=device_info.device_id,
            auto_connect=False,
        )
        self.screen_mirror = screen_mirror or AndroidScrcpyStreamer(device_id=device_info.device_id)
        logger.info("单设备控制器初始化完成: %s", device_info.to_dict())

    @property
    def device_id(self) -> str:
        return self.device_info.device_id

    def update_device_info(self, device_info: AndroidDeviceInfo) -> None:
        logger.info("更新设备信息: %s", device_info.to_dict())
        self.device_info = device_info

    def connect_remote(self, force_reconnect: bool = False) -> AndroidRemoteOperationController:
        logger.info("开始连接远程操作控制器: device_id=%s, force_reconnect=%s", self.device_id, force_reconnect)
        self.remote_operation.connect(force_reconnect=force_reconnect)
        logger.info("远程操作控制器连接成功: %s", self.device_id)
        return self.remote_operation

    def start_mirror(
        self,
        max_fps: Optional[int] = None,
        bit_rate: Optional[int] = None,
        max_size: Optional[int] = None,
        video_codec: Optional[str] = None,
    ) -> AndroidScrcpyStreamer:
        logger.info(
            "开始启动镜像流: device_id=%s, max_fps=%s, bit_rate=%s, max_size=%s, video_codec=%s",
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

        ##解锁屏幕
        self.remote_operation.unlock()
        self.screen_mirror.start()
        logger.info("镜像流启动成功: %s", self.device_id)
        return self.screen_mirror

    def stop_mirror(self) -> None:
        logger.info("开始停止镜像流: %s", self.device_id)
        self.screen_mirror.stop()
        self.remote_operation.screen_off()
        logger.info("镜像流已停止: %s", self.device_id)

    def snapshot(self, format: str = "opencv") -> Any:
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
        logger.info("开始关闭单设备控制器: %s", self.device_id)
        self.stop_mirror()
        self.remote_operation.disconnect()
        logger.info("单设备控制器已关闭: %s", self.device_id)


# if __name__ == "__main__":
#     controller = AndroidController(device_info=AndroidDeviceInfo())
