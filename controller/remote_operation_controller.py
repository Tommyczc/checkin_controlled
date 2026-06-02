from __future__ import annotations

import time
from typing import Any, Optional

import uiautomator2 as u2


class RemoteOperationController:
    """基于 uiautomator2 的单设备远程操作控制器。"""

    def __init__(self, device_id: str, auto_connect: bool = False, healthcheck: bool = True):
        self.device_id = device_id
        self.auto_connect = auto_connect
        self.healthcheck = healthcheck
        self._device: Optional[u2.Device] = None

        if self.auto_connect:
            self.connect()

    @property
    def device(self) -> u2.Device:
        if self._device is None:
            raise RuntimeError("uiautomator2 尚未连接，请先调用 connect()")
        return self._device

    def connect(self, force_reconnect: bool = False) -> u2.Device:
        """初始化并返回 uiautomator2 设备连接。"""
        if self._device is not None and not force_reconnect:
            return self._device

        device = u2.connect(self.device_id)
        if self.healthcheck:
            device.healthcheck()

        self._device = device
        return device

    def disconnect(self) -> None:
        """当前控制器为无状态客户端，这里只清理引用。"""
        self._device = None

    def is_connected(self) -> bool:
        return self._device is not None

    def info(self) -> dict[str, Any]:
        return dict(self.device.info)

    def app_current(self) -> dict[str, Any]:
        return dict(self.device.app_current())

    def screenshot(self, format: str = "opencv") -> Any:
        return self.device.screenshot(format=format)

    def dump_hierarchy(self, compressed: bool = False, pretty: bool = False) -> str:
        return self.device.dump_hierarchy(compressed=compressed, pretty=pretty)

    def screen_on(self) -> None:
        self.device.screen_on()

    def screen_off(self) -> None:
        self.device.screen_off()

    def unlock(self) -> None:
        self.device.unlock()

    def click(self, x: int, y: int) -> None:
        self.device.click(x, y)

    def long_click(self, x: int, y: int, duration: float = 0.5) -> None:
        self.device.long_click(x, y, duration=duration)

    def swipe(self, fx: int, fy: int, tx: int, ty: int, duration: float = 0.1) -> None:
        self.device.swipe(fx, fy, tx, ty, duration=duration)

    def press(self, key: str | int) -> None:
        self.device.press(key)

    def set_text(self, text: str, clear: bool = True) -> None:
        if clear:
            self.device.clear_text()
        self.device.send_keys(text)

    def start_app(self, package_name: str, stop: bool = False) -> None:
        self.device.app_start(package_name, stop=stop)

    def stop_app(self, package_name: str) -> None:
        self.device.app_stop(package_name)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)
