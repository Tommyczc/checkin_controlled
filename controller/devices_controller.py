from __future__ import annotations

from typing import Optional

import adbutils

from controller.android_controller import AndroidController, AndroidDeviceInfo
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()


class DevicesController:
    """安卓多设备管理器。负责扫描、初始化、缓存与回收设备控制器。"""

    def __init__(self, auto_connect_remote: bool = False):
        self.auto_connect_remote = auto_connect_remote
        self._devices: dict[str, AndroidController] = {}
        logger.info("多设备控制器初始化完成，auto_connect_remote=%s", auto_connect_remote)

    def refresh_devices(self) -> dict[str, AndroidController]:
        """扫描当前 USB/ADB 设备，并同步本地缓存。"""
        logger.info("开始刷新安卓设备列表")
        next_devices: dict[str, AndroidController] = {}

        for adb_device in adbutils.adb.device_list():
            if not self._is_usb_device(adb_device.serial):
                continue

            device_info = self._build_device_info(adb_device.serial)
            controller = self._devices.get(adb_device.serial)

            if controller is None:
                logger.info("发现新设备接入: %s (%s)", adb_device.serial, device_info.display_name())
                controller = AndroidController(device_info=device_info)
                if self.auto_connect_remote:
                    try:
                        controller.connect_remote()
                    except Exception as exc:
                        logger.warning("设备 %s 的 uiautomator2 初始化失败: %s", adb_device.serial, exc)
            else:
                controller.update_device_info(device_info)

            next_devices[adb_device.serial] = controller

        removed_ids = set(self._devices) - set(next_devices)
        for device_id in removed_ids:
            try:
                logger.info("检测到设备离线，开始清理: %s", device_id)
                self._devices[device_id].close()
            except Exception as exc:
                logger.warning("清理离线设备 %s 失败: %s", device_id, exc)

        self._devices = next_devices
        logger.info("设备刷新完成，当前在线设备数: %s", len(self._devices))
        return dict(self._devices)

    def list_devices(self, refresh: bool = True) -> list[AndroidController]:
        if refresh:
            self.refresh_devices()
        return list(self._devices.values())

    def get_device(self, device_id: str, refresh: bool = False) -> Optional[AndroidController]:
        if refresh:
            self.refresh_devices()
        controller = self._devices.get(device_id)
        if controller is None:
            logger.warning("请求的设备不存在或未在线: %s", device_id)
        return controller

    def get_payloads(self, refresh: bool = True) -> list[dict]:
        return [controller.to_server_payload() for controller in self.list_devices(refresh=refresh)]

    def close(self) -> None:
        logger.info("开始关闭多设备控制器，设备数: %s", len(self._devices))
        for controller in self._devices.values():
            controller.close()
        self._devices.clear()
        logger.info("多设备控制器已关闭")

    def _build_device_info(self, device_id: str) -> AndroidDeviceInfo:
        adb_device = adbutils.adb.device(serial=device_id)

        def getprop(prop_name: str) -> str:
            try:
                return adb_device.shell(f"getprop {prop_name}").strip()
            except Exception:
                return ""

        manufacturer = getprop("ro.product.manufacturer")
        brand = getprop("ro.product.brand") or manufacturer
        model = getprop("ro.product.model")
        android_version = getprop("ro.build.version.release")
        sdk_version = getprop("ro.build.version.sdk")

        return AndroidDeviceInfo(
            device_id=device_id,
            brand=brand,
            model=model,
            manufacturer=manufacturer,
            android_version=android_version,
            sdk_version=sdk_version,
            state="device",
        )

    @staticmethod
    def _is_usb_device(device_id: str) -> bool:
        return ":" not in device_id

devices=DevicesController()
