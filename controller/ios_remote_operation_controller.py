from __future__ import annotations

import socket
import threading
import time
from typing import Any, Optional

from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()


class IosRemoteOperationController:
    """基于 WebDriverAgent 的单台 iOS 设备远程操作控制器。"""

    def __init__(
        self,
        device_id: str,
        udid: Optional[str] = None,
        wda_port: int = 8100,
        device_wda_port: int = 8100,
        local_wda_port: Optional[int] = None,
        auto_connect: bool = False,
        wait_timeout: float = 10.0,
        prefer_local_http: bool = True,
    ):
        self.device_id = device_id
        self.udid = udid or device_id
        self.local_wda_port = local_wda_port if local_wda_port is not None else wda_port
        self.device_wda_port = device_wda_port
        self.wda_port = self.local_wda_port
        self.auto_connect = auto_connect
        self.wait_timeout = wait_timeout
        self.prefer_local_http = prefer_local_http
        self._client: Optional[Any] = None
        self._client_transport = ""
        self._action_lock = threading.Lock()
        self._window_size: Optional[tuple[int, int]] = None
        self._scale: Optional[int] = None
        logger.info(
            "iOS 远程操作控制器初始化完成: device_id=%s, udid=%s, local_wda_port=%s, device_wda_port=%s, auto_connect=%s, prefer_local_http=%s",
            device_id,
            self.udid,
            self.local_wda_port,
            self.device_wda_port,
            auto_connect,
            prefer_local_http,
        )

        if self.auto_connect:
            self.connect()

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("WDA 尚未连接，请先调用 connect()")
        return self._client

    def connect(self, force_reconnect: bool = False) -> Any:
        if self._client is not None and not force_reconnect:
            logger.info("复用已有 WDA 连接: %s", self.device_id)
            return self._client

        try:
            import wda
        except ImportError as exc:
            raise RuntimeError("未安装 facebook-wda，请先安装 `facebook-wda` 后再使用 iOS 远程控制") from exc

        logger.info(
            "开始建立 WDA 连接: device_id=%s, udid=%s, local_wda_port=%s, device_wda_port=%s",
            self.device_id,
            self.udid,
            self.local_wda_port,
            self.device_wda_port,
        )
        if self.prefer_local_http and _is_tcp_port_open("127.0.0.1", self.local_wda_port):
            wda_url = f"http://127.0.0.1:{self.local_wda_port}"
            client = wda.Client(wda_url)
            self._client_transport = wda_url
        else:
            client = wda.USBClient(self.udid, port=self.device_wda_port)
            self._client_transport = f"http+usbmux://{self.udid}:{self.device_wda_port}"

        wait_ready = getattr(client, "wait_ready", None)
        if callable(wait_ready):
            try:
                ready = wait_ready(timeout=self.wait_timeout, noprint=True)
            except TypeError:
                ready = wait_ready(timeout=self.wait_timeout)
            if not ready:
                raise RuntimeError(f"WDA 在 {self.wait_timeout:.1f}s 内未就绪，请确认已通过 xctest/runwda 启动")
        else:
            status = getattr(client, "status", None)
            if callable(status):
                status()

        self._client = client
        self._window_size = None
        self._scale = None
        logger.info("WDA 连接成功: device_id=%s, transport=%s", self.device_id, self._client_transport)
        return client

    def disconnect(self) -> None:
        if self._client is not None:
            logger.info("断开 WDA 连接: %s", self.device_id)
        self._client = None
        self._client_transport = ""
        self._window_size = None
        self._scale = None

    def is_connected(self) -> bool:
        return self._client is not None

    def info(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        info = _read_attr_or_call(self.client, "info")
        status = _read_attr_or_call(self.client, "status")
        if isinstance(info, dict):
            result["info"] = info
        elif info is not None:
            result["info"] = str(info)
        if isinstance(status, dict):
            result["status"] = status
        elif status is not None:
            result["status"] = str(status)
        return result

    def app_current(self) -> dict[str, Any]:
        current = _call_first(self.client, ["app_current"])
        if isinstance(current, dict):
            return current
        return {"value": current}

    def screenshot(self, format: str = "pillow") -> Any:
        screenshot = getattr(self.client, "screenshot", None)
        if not callable(screenshot):
            raise RuntimeError("当前 WDA client 不支持 screenshot")
        return screenshot()

    def dump_hierarchy(self, compressed: bool = False, pretty: bool = False) -> str:
        source = getattr(self.client, "source", None)
        if not callable(source):
            raise RuntimeError("当前 WDA client 不支持 source")
        return source()

    def screen_on(self) -> None:
        logger.info("执行 iOS 亮屏/保活操作: %s", self.device_id)
        _call_first(self.client, ["healthcheck", "status"])

    def screen_off(self) -> None:
        logger.info("执行 iOS 锁屏操作: %s", self.device_id)
        _call_first(self.client, ["lock"])

    def unlock(self) -> None:
        logger.info("执行 iOS 解锁操作: %s", self.device_id)
        try:
            _call_first(self.client, ["unlock"])
        except AttributeError:
            _call_first(self.client, ["healthcheck", "status"])

    def click(self, x: int, y: int) -> None:
        def action() -> None:
            normalized_x, normalized_y = self._normalize_point(x, y)
            logger.info("执行 iOS 点击: device_id=%s, x=%s, y=%s", self.device_id, normalized_x, normalized_y)
            _call_first(self._action_target(["click", "tap"]), ["click", "tap"], normalized_x, normalized_y)

        self._run_action(action)

    def long_click(self, x: int, y: int, duration: float = 0.5) -> None:
        normalized_duration = _clamp_float(duration, 0.1, 10.0)

        def action() -> None:
            normalized_x, normalized_y = self._normalize_point(x, y)
            logger.info(
                "执行 iOS 长按: device_id=%s, x=%s, y=%s, duration=%s",
                self.device_id,
                normalized_x,
                normalized_y,
                normalized_duration,
            )
            target = self._action_target(["tap_hold", "long_click", "touch_and_hold", "click"])
            try:
                _call_first(
                    target,
                    ["tap_hold", "long_click", "touch_and_hold"],
                    normalized_x,
                    normalized_y,
                    normalized_duration,
                )
            except AttributeError:
                _call_first(target, ["click"], normalized_x, normalized_y)

        self._run_action(action)

    def swipe(self, fx: int, fy: int, tx: int, ty: int, duration: float = 0.1) -> None:
        original = (fx, fy, tx, ty)
        normalized_duration = _clamp_float(duration, 0.05, 3.0)

        def action() -> None:
            normalized_fx, normalized_fy = self._normalize_point(fx, fy)
            normalized_tx, normalized_ty = self._normalize_point(tx, ty)
            logger.info(
                "执行 iOS 滑动: device_id=%s, original=%s, normalized=(%s, %s, %s, %s), duration=%s",
                self.device_id,
                original,
                normalized_fx,
                normalized_fy,
                normalized_tx,
                normalized_ty,
                normalized_duration,
            )
            target = self._action_target(["swipe", "drag"])
            try:
                _call_first(
                    target,
                    ["swipe"],
                    normalized_fx,
                    normalized_fy,
                    normalized_tx,
                    normalized_ty,
                    normalized_duration,
                )
            except AttributeError:
                _call_first(
                    target,
                    ["drag"],
                    normalized_fx,
                    normalized_fy,
                    normalized_tx,
                    normalized_ty,
                    normalized_duration,
                )

        self._run_action(action)

    def press(self, key: str | int) -> None:
        normalized = str(key).lower()
        if normalized in {"home", "homebutton"}:
            _call_first(self.client, ["home"])
            return
        if normalized in {"lock", "power"}:
            self.screen_off()
            return
        if normalized in {"unlock"}:
            self.unlock()
            return
        raise ValueError(f"iOS 暂不支持按键: {key}")

    def set_text(self, text: str, clear: bool = True) -> None:
        target = self._action_target(["send_keys", "set_text"])
        if clear:
            try:
                _call_first(target, ["clear_text", "clear"])
            except AttributeError:
                logger.info("当前 iOS 输入目标不支持 clear，跳过清空文本: %s", self.device_id)
        _call_first(target, ["send_keys", "set_text"], text)

    def start_app(self, package_name: str, stop: bool = False) -> None:
        logger.info("启动 iOS 应用: device_id=%s, bundle_id=%s, stop=%s", self.device_id, package_name, stop)
        if stop:
            try:
                self.stop_app(package_name)
            except Exception as exc:
                logger.info("启动前停止 iOS 应用失败，继续启动: device_id=%s, error=%s", self.device_id, exc)

        try:
            _call_first(self.client, ["app_activate", "app_launch"], package_name)
            return
        except AttributeError:
            pass

        session = self._session()
        try:
            _call_first(session, ["app_activate", "app_launch"], package_name)
        except AttributeError:
            session_factory = getattr(self.client, "session", None)
            if not callable(session_factory):
                raise
            session_factory(package_name)

    def stop_app(self, package_name: str) -> None:
        logger.info("停止 iOS 应用: device_id=%s, bundle_id=%s", self.device_id, package_name)
        try:
            _call_first(self.client, ["app_terminate", "app_stop"], package_name)
            return
        except AttributeError:
            pass

        session = self._session()
        _call_first(session, ["app_terminate", "app_stop"], package_name)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def window_size(self, force_refresh: bool = False) -> Optional[tuple[int, int]]:
        if self._window_size is not None and not force_refresh:
            return self._window_size

        size_method = getattr(self.client, "window_size", None)
        if not callable(size_method):
            return None

        size = size_method()
        width = _read_dimension(size, "width", 0)
        height = _read_dimension(size, "height", 1)
        if width <= 0 or height <= 0:
            return None

        self._window_size = (width, height)
        logger.info("获取 iOS WDA 窗口尺寸成功: device_id=%s, window_size=%s", self.device_id, self._window_size)
        return self._window_size

    def scale(self) -> Optional[int]:
        if self._scale is not None:
            return self._scale

        value = getattr(self.client, "scale", None)
        try:
            scale = int(value() if callable(value) else value)
        except Exception as exc:
            logger.info("获取 iOS 屏幕 scale 失败，按 1 处理: device_id=%s, error=%s", self.device_id, exc)
            scale = 1

        self._scale = max(1, scale)
        return self._scale

    def _session(self) -> Any:
        session_factory = getattr(self.client, "session", None)
        if not callable(session_factory):
            raise RuntimeError("当前 WDA client 不支持 session")
        return session_factory()

    def _action_target(self, method_names: list[str]) -> Any:
        if any(callable(getattr(self.client, name, None)) for name in method_names):
            return self.client
        return self._session()

    def _run_action(self, action) -> None:
        with self._action_lock:
            try:
                action()
            except Exception as exc:
                logger.warning(
                    "iOS WDA 动作执行异常，尝试重连后重试一次: device_id=%s, transport=%s, error=%s",
                    self.device_id,
                    self._client_transport,
                    exc,
                )
                self.connect(force_reconnect=True)
                action()

    def _normalize_point(self, x: int, y: int) -> tuple[int, int]:
        window_size = self.window_size()
        if window_size is None:
            return int(x), int(y)

        width, height = window_size
        original = (int(x), int(y))
        normalized_x, normalized_y = original
        scale = self.scale() or 1

        if scale > 1 and (normalized_x >= width or normalized_y >= height):
            scaled_x = round(normalized_x / scale)
            scaled_y = round(normalized_y / scale)
            if scaled_x <= width * 1.2 and scaled_y <= height * 1.2:
                normalized_x, normalized_y = scaled_x, scaled_y

        clamped_x = _clamp_int(normalized_x, 0, max(0, width - 1))
        clamped_y = _clamp_int(normalized_y, 0, max(0, height - 1))
        if (clamped_x, clamped_y) != original:
            logger.info(
                "iOS 触控坐标已归一化: device_id=%s, original=%s, normalized=(%s, %s), window_size=%s, scale=%s",
                self.device_id,
                original,
                clamped_x,
                clamped_y,
                window_size,
                scale,
            )
        return clamped_x, clamped_y


def _read_attr_or_call(target: Any, name: str) -> Any:
    value = getattr(target, name, None)
    if callable(value):
        return value()
    return value


def _call_first(target: Any, names: list[str], *args, **kwargs) -> Any:
    for name in names:
        value = getattr(target, name, None)
        if callable(value):
            return value(*args, **kwargs)
    raise AttributeError(f"{target!r} 不支持方法: {', '.join(names)}")


def _read_dimension(value: Any, name: str, index: int) -> int:
    named = getattr(value, name, None)
    if named is not None:
        return int(named)
    if isinstance(value, dict) and name in value:
        return int(value[name])
    return int(value[index])


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(float(value), maximum))


def _is_tcp_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False
