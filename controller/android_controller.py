from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from controller.remote_operation_controller import RemoteOperationController
from controller.screen_mirror_controller import ScrcpyStreamer
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
        remote_operation: Optional[RemoteOperationController] = None,
        screen_mirror: Optional[ScrcpyStreamer] = None,
    ):
        self.device_info = device_info
        self.remote_operation = remote_operation or RemoteOperationController(
            device_id=device_info.device_id,
            auto_connect=False,
        )
        self.screen_mirror = screen_mirror or ScrcpyStreamer(device_id=device_info.device_id)

    @property
    def device_id(self) -> str:
        return self.device_info.device_id

    def update_device_info(self, device_info: AndroidDeviceInfo) -> None:
        self.device_info = device_info

    def connect_remote(self, force_reconnect: bool = False) -> RemoteOperationController:
        self.remote_operation.connect(force_reconnect=force_reconnect)
        return self.remote_operation

    def start_mirror(
        self,
        max_fps: Optional[int] = None,
        bit_rate: Optional[int] = None,
        max_size: Optional[int] = None,
        video_codec: Optional[str] = None,
    ) -> ScrcpyStreamer:
        if max_fps is not None:
            self.screen_mirror.max_fps = max_fps
        if bit_rate is not None:
            self.screen_mirror.bit_rate = bit_rate
        if max_size is not None:
            self.screen_mirror.max_size = max_size
        if video_codec is not None:
            self.screen_mirror.video_codec = video_codec

        self.screen_mirror.start()
        return self.screen_mirror

    def stop_mirror(self) -> None:
        self.screen_mirror.stop()

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
        self.stop_mirror()
        self.remote_operation.disconnect()


# if __name__ == "__main__":
#     controller = AndroidController(device_info=AndroidDeviceInfo())