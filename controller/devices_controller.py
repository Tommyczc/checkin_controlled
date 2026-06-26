from __future__ import annotations

import asyncio
import inspect
import socket
import threading
from dataclasses import dataclass
from typing import Optional

import adbutils

from controller.android_controller import AndroidController, AndroidDeviceInfo
from controller.ios_controller import IosController, IosDeviceInfo
from controller.android_screen_mirror_controller import adb_server_host, adb_server_port, ensure_adb_server
from controller.config_controller import config
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()

DeviceController = AndroidController | IosController


@dataclass(frozen=True)
class IosPortAssignment:
    local_wda_port: int
    local_mjpeg_port: int
    local_replaykit_port: int
    device_wda_port: int
    device_mjpeg_port: int
    device_replaykit_port: int


class DevicesController:
    """多设备管理器。负责扫描、初始化、缓存与回收 Android/iOS 设备控制器。"""

    def __init__(self, auto_connect_remote: bool = False):
        self.auto_connect_remote = auto_connect_remote
        self._devices: dict[str, DeviceController] = {}
        self._ios_port_assignments: dict[str, IosPortAssignment] = {}
        logger.info("多设备控制器初始化完成，auto_connect_remote=%s", auto_connect_remote)

    def refresh_devices(self) -> dict[str, DeviceController]:
        """扫描当前 Android ADB 设备和 iOS usbmux 设备，并同步本地缓存。"""
        logger.info("开始刷新设备列表")
        next_devices: dict[str, DeviceController] = {}

        self._refresh_android_devices(next_devices)
        self._refresh_ios_devices(next_devices)

        removed_ids = set(self._devices) - set(next_devices)
        for device_id in removed_ids:
            try:
                logger.info("检测到设备离线，开始清理: %s", device_id)
                self._devices[device_id].close()
                if device_id.startswith("ios:"):
                    self._ios_port_assignments.pop(device_id.removeprefix("ios:"), None)
            except Exception as exc:
                logger.warning("清理离线设备 %s 失败: %s", device_id, exc)

        self._devices = next_devices
        logger.info("设备刷新完成，当前在线设备数: %s", len(self._devices))
        return dict(self._devices)

    def list_devices(self, refresh: bool = True) -> list[DeviceController]:
        if refresh:
            self.refresh_devices()
        return list(self._devices.values())

    def get_device(self, device_id: str, refresh: bool = False) -> Optional[DeviceController]:
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
        self._ios_port_assignments.clear()
        logger.info("多设备控制器已关闭")

    def _refresh_android_devices(self, next_devices: dict[str, DeviceController]) -> None:
        logger.info("开始刷新安卓设备列表")
        for adb_device in self._device_list():
            if not self._is_usb_device(adb_device.serial):
                continue

            device_info = self._build_device_info(adb_device.serial)
            controller = self._devices.get(adb_device.serial)

            if not isinstance(controller, AndroidController):
                logger.info("发现新安卓设备接入: %s (%s)", adb_device.serial, device_info.display_name())
                controller = AndroidController(device_info=device_info)
                if self.auto_connect_remote:
                    try:
                        controller.connect_remote()
                    except Exception as exc:
                        logger.warning("设备 %s 的 uiautomator2 初始化失败: %s", adb_device.serial, exc)
            else:
                controller.update_device_info(device_info)

            next_devices[adb_device.serial] = controller

    def _refresh_ios_devices(self, next_devices: dict[str, DeviceController]) -> None:
        logger.info("开始刷新 iOS 设备列表")
        for ios_device in self._ios_device_list():
            udid = _ios_device_udid(ios_device)
            if not udid:
                continue

            try:
                device_info = self._build_ios_device_info(udid, ios_device)
            except Exception as exc:
                logger.warning("构建 iOS 设备信息失败，跳过该设备: udid=%s, error=%s", udid, exc)
                continue

            controller = self._devices.get(device_info.device_id)

            if not isinstance(controller, IosController):
                logger.info("发现新 iOS 设备接入: %s (%s)", udid, device_info.display_name())
                controller = IosController(device_info=device_info)
                if self.auto_connect_remote:
                    try:
                        controller.connect_remote()
                    except Exception as exc:
                        logger.warning("设备 %s 的 WDA 初始化失败: %s", device_info.device_id, exc)
            else:
                controller.update_device_info(device_info)

            next_devices[device_info.device_id] = controller

    def _build_device_info(self, device_id: str) -> AndroidDeviceInfo:
        ensure_adb_server()
        adb_device = _adb_client().device(serial=device_id)

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

    def _build_ios_device_info(self, udid: str, ios_device) -> IosDeviceInfo:
        lockdown_info = _ios_lockdown_info(udid)
        port_assignment = self._ios_port_assignment(udid)
        connection_type = str(
            getattr(ios_device, "connection_type", "")
            or getattr(ios_device, "connection_type_name", "")
            or getattr(ios_device, "ConnectionType", "")
            or ""
        )

        return IosDeviceInfo(
            device_id=_ios_device_id(udid),
            udid=udid,
            name=str(lockdown_info.get("name") or ""),
            product_type=str(lockdown_info.get("product_type") or ""),
            ios_version=str(lockdown_info.get("ios_version") or ""),
            connection_type=connection_type,
            state="device",
            local_wda_port=port_assignment.local_wda_port,
            local_mjpeg_port=port_assignment.local_mjpeg_port,
            local_replaykit_port=port_assignment.local_replaykit_port,
            device_wda_port=port_assignment.device_wda_port,
            device_mjpeg_port=port_assignment.device_mjpeg_port,
            device_replaykit_port=port_assignment.device_replaykit_port,
        )

    def _ios_port_assignment(self, udid: str) -> IosPortAssignment:
        existing = self._ios_port_assignments.get(udid)
        if existing is not None:
            return existing

        device_wda_port = _config_int("ios.wda.device_port", 8100)
        device_mjpeg_port = _config_int("ios.mjpeg.device_port", 9100)
        device_replaykit_port = _config_int("ios.replaykit.device_port", 27777)
        local_wda_base = _config_int("ios.wda.local_port_base", 18100)
        local_mjpeg_base = _config_int("ios.mjpeg.local_port_base", 19100)
        local_replaykit_base = _config_int("ios.replaykit.local_port_base", 27777)

        used_wda_mjpeg_ports = set()
        used_replaykit_ports = set()
        for assignment in self._ios_port_assignments.values():
            used_wda_mjpeg_ports.add(assignment.local_wda_port)
            used_wda_mjpeg_ports.add(assignment.local_mjpeg_port)
            used_replaykit_ports.add(assignment.local_replaykit_port)

        local_wda_port: Optional[int] = None
        local_mjpeg_port: Optional[int] = None
        for offset in range(0, 1000):
            wda_candidate = local_wda_base + offset
            mjpeg_candidate = local_mjpeg_base + offset
            if not _valid_port(wda_candidate) or not _valid_port(mjpeg_candidate):
                continue
            if wda_candidate in used_wda_mjpeg_ports or mjpeg_candidate in used_wda_mjpeg_ports:
                continue
            if _is_local_tcp_port_open(wda_candidate) or _is_local_tcp_port_open(mjpeg_candidate):
                logger.info(
                    "iOS 本机端口已被占用，跳过端口候选: udid=%s, local_wda_port=%s, local_mjpeg_port=%s",
                    udid,
                    wda_candidate,
                    mjpeg_candidate,
                )
                continue

            local_wda_port = wda_candidate
            local_mjpeg_port = mjpeg_candidate
            break

        local_replaykit_port: Optional[int] = None
        reserved_ports = used_wda_mjpeg_ports | used_replaykit_ports
        if local_wda_port is not None:
            reserved_ports.add(local_wda_port)
        if local_mjpeg_port is not None:
            reserved_ports.add(local_mjpeg_port)

        for offset in range(0, 1000):
            replaykit_candidate = local_replaykit_base + offset
            if not _valid_port(replaykit_candidate):
                continue
            if replaykit_candidate in reserved_ports:
                continue
            local_replaykit_port = replaykit_candidate
            break

        if local_wda_port is None or local_mjpeg_port is None or local_replaykit_port is None:
            raise RuntimeError(
                f"无法为 iOS 设备分配本机转发端口: udid={udid}, "
                f"wda_base={local_wda_base}, mjpeg_base={local_mjpeg_base}, replaykit_base={local_replaykit_base}"
            )

        assignment = IosPortAssignment(
            local_wda_port=local_wda_port,
            local_mjpeg_port=local_mjpeg_port,
            local_replaykit_port=local_replaykit_port,
            device_wda_port=device_wda_port,
            device_mjpeg_port=device_mjpeg_port,
            device_replaykit_port=device_replaykit_port,
        )
        self._ios_port_assignments[udid] = assignment
        logger.info("已分配 iOS 本机端口: udid=%s, assignment=%s", udid, assignment)
        return assignment

    @staticmethod
    def _is_usb_device(device_id: str) -> bool:
        return ":" not in device_id

    @staticmethod
    def _device_list():
        ensure_adb_server()
        try:
            return _adb_client().device_list()
        except Exception as exc:
            logger.warning("adbutils 扫描设备失败，尝试恢复 adb server 后重试: %s", exc)
            ensure_adb_server(allow_reset=_should_reset_adb(exc))
            return _adb_client().device_list()

    @staticmethod
    def _ios_device_list():
        try:
            return _run_async(_list_ios_devices_async)
        except ImportError:
            logger.info("未安装 pymobiledevice3，跳过 iOS 设备扫描")
            return []
        except Exception as exc:
            logger.warning("pymobiledevice3 扫描 iOS 设备失败，跳过本轮 iOS 设备刷新: %s", exc)
            return []


def _should_reset_adb(exc: Exception) -> bool:
    message = str(exc).lower()
    return "adb server version" in message or "doesn't match this client" in message or "protocol fault" in message


def _adb_client():
    return adbutils.AdbClient(host=adb_server_host(), port=adb_server_port())


def _config_int(name: str, default: int) -> int:
    value = config.get(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("配置项不是有效整数，使用默认值: %s=%s, default=%s", name, value, default)
        return default


def _valid_port(port: int) -> bool:
    return 1 <= int(port) <= 65535


def _is_local_tcp_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.2):
            return True
    except OSError:
        return False


def _ios_device_id(udid: str) -> str:
    return f"ios:{udid}"


def _ios_device_udid(ios_device) -> str:
    return str(
        getattr(ios_device, "serial", "")
        or getattr(ios_device, "udid", "")
        or getattr(ios_device, "identifier", "")
        or ""
    )


async def _list_ios_devices_async():
    from pymobiledevice3.usbmux import list_devices

    return await list_devices()


def _ios_lockdown_info(udid: str) -> dict[str, str]:
    try:
        return _run_async(lambda: _ios_lockdown_info_async(udid))
    except ImportError:
        return {}
    except Exception as exc:
        logger.info("读取 iOS lockdown 信息失败，使用 usbmux 基础信息: udid=%s, error=%s", udid, exc)
        return {}


async def _ios_lockdown_info_async(udid: str) -> dict[str, str]:
    from pymobiledevice3.lockdown import create_using_usbmux

    try:
        lockdown = await create_using_usbmux(serial=udid, autopair=False)
    except TypeError:
        lockdown = await create_using_usbmux(serial=udid)

    async with lockdown:
        return {
            "name": await _lockdown_text(lockdown, "DeviceName", ["device_name", "name"]),
            "product_type": await _lockdown_text(lockdown, "ProductType", ["product_type", "product"]),
            "ios_version": await _lockdown_text(lockdown, "ProductVersion", ["product_version", "ios_version"]),
        }


async def _lockdown_text(lockdown, key: str, attr_names: list[str]) -> str:
    getter = getattr(lockdown, "get_value", None)
    if callable(getter):
        for args, kwargs in [
            ((), {"key": key}),
            ((None, key), {}),
            ((key,), {}),
        ]:
            try:
                value = getter(*args, **kwargs)
                if inspect.isawaitable(value):
                    value = await value
                if value:
                    return str(value)
            except TypeError:
                continue
            except Exception:
                break

    return _first_text_attr(lockdown, attr_names)


def _first_text_attr(target, names: list[str]) -> str:
    for name in names:
        value = getattr(target, name, None)
        if value:
            return str(value)
    return ""


def _run_async(async_factory):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(async_factory())

    result: dict[str, object] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(async_factory())
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in result:
        raise result["error"]
    return result.get("value")

devices=DevicesController()
