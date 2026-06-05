from __future__ import annotations

import time
from typing import Any, Optional

import uiautomator2 as u2
from controller.screen_mirror_controller import ensure_adb_server
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()


class RemoteOperationController:
    """基于 uiautomator2 的单设备远程操作控制器。"""

    def __init__(self, device_id: str, auto_connect: bool = False, healthcheck: bool = True):
        self.device_id = device_id
        self.auto_connect = auto_connect
        self.healthcheck = healthcheck
        self._device: Optional[u2.Device] = None
        logger.info(
            "远程操作控制器初始化完成: device_id=%s, auto_connect=%s, healthcheck=%s",
            device_id,
            auto_connect,
            healthcheck,
        )

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
            logger.info("复用已有 uiautomator2 连接: %s", self.device_id)
            return self._device

        logger.info("开始建立 uiautomator2 连接: %s", self.device_id)
        ensure_adb_server()
        try:
            device = u2.connect(self.device_id)
        except Exception as exc:
            logger.warning("uiautomator2 连接失败，尝试恢复 adb server 后重试: device_id=%s, error=%s", self.device_id, exc)
            ensure_adb_server(allow_reset=_should_reset_adb(exc))
            device = u2.connect(self.device_id)
        if self.healthcheck:
            healthcheck = getattr(device, "healthcheck", None)
            if callable(healthcheck):
                healthcheck()
            else:
                logger.info(
                    "当前 uiautomator2 连接对象不支持 healthcheck，跳过初始化检查: %s",
                    self.device_id,
                )

        self._device = device
        logger.info("uiautomator2 连接成功: %s", self.device_id)
        return device

    def disconnect(self) -> None:
        """当前控制器为无状态客户端，这里只清理引用。"""
        if self._device is not None:
            logger.info("断开 uiautomator2 连接: %s", self.device_id)
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
        logger.info("执行亮屏操作: %s", self.device_id)
        self.device.screen_on()

    def screen_off(self) -> None:
        logger.info("执行息屏操作: %s", self.device_id)
        self.device.screen_off()

    def unlock(self) -> None:
        logger.info("执行解锁操作: %s", self.device_id)
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
        logger.info("启动应用: device_id=%s, package_name=%s, stop=%s", self.device_id, package_name, stop)
        self.device.app_start(package_name, stop=stop)

    def stop_app(self, package_name: str) -> None:
        logger.info("停止应用: device_id=%s, package_name=%s", self.device_id, package_name)
        self.device.app_stop(package_name)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)


def _should_reset_adb(exc: Exception) -> bool:
    message = str(exc).lower()
    return "adb server version" in message or "doesn't match this client" in message or "protocol fault" in message
