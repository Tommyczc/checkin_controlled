from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO, Generator, Optional

import av
import cv2
import numpy as np
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()

_ADB_SERVER_LOCK = threading.RLock()
_ADB_DAEMON_ERRORS = (
    "daemon not running",
    "cannot connect to daemon",
    "cannot connect to adb daemon",
    "failed to check server version",
)
_ADB_RESET_ERRORS = (
    "adb server version",
    "doesn't match this client",
    "protocol fault",
)


class _StreamDrainer(threading.Thread):
    """后台消费进程日志，避免管道堵塞。"""

    def __init__(self, stream):
        super().__init__(daemon=True)
        self._stream = stream
        self._chunks: list[str] = []

    def run(self) -> None:
        if self._stream is None:
            return

        while True:
            chunk = self._stream.readline()
            if not chunk:
                break
            self._chunks.append(chunk.decode("utf-8", errors="replace"))

    def get_text(self) -> str:
        return "".join(self._chunks).strip()


class ScrcpyStreamer:
    """基于 scrcpy standalone server 的单设备屏幕视频流控制器。"""

    def __init__(
        self,
        device_id: str,
        max_fps: int = 30,
        bit_rate: int = 4_000_000,
        max_size: Optional[int] = 0,
        video_codec: str = "h264",
        server_version: Optional[str] = None,
    ):
        self.device_id = device_id
        self.max_fps = max_fps
        self.bit_rate = bit_rate
        self.max_size = max_size
        self.video_codec = video_codec
        self.server_version = server_version

        self.server_process: Optional[subprocess.Popen] = None
        self._server_log_drainers: list[_StreamDrainer] = []
        self._video_socket: Optional[socket.socket] = None
        self._video_stream: Optional[BinaryIO] = None
        self._local_port: Optional[int] = None
        self._video_size_lock = threading.Lock()
        self._video_size: Optional[tuple[int, int]] = None
        self._remote_server_path = "/data/local/tmp/scrcpy-server.jar"
        logger.info(
            "ScrcpyStreamer 初始化完成: device_id=%s, max_fps=%s, bit_rate=%s, max_size=%s, video_codec=%s, server_version=%s",
            device_id,
            max_fps,
            bit_rate,
            max_size,
            video_codec,
            self.server_version,
        )

    def start(self) -> None:
        """启动 scrcpy standalone server，并建立视频 socket。"""
        if self.server_process is not None:
            raise RuntimeError("Scrcpy stream already running")

        self._set_video_size(None)
        logger.info("开始启动 scrcpy 镜像流: %s", self.device_id)
        _ensure_binary("adb")
        _ensure_binary("scrcpy")
        ensure_adb_server()
        self._prime_video_size()

        server_path = _resolve_scrcpy_server_path()
        self._local_port = _pick_free_port()

        self._push_server(server_path)
        self._forward_socket(self._local_port)
        self._start_server_process()
        self._connect_video_socket(self._local_port)
        logger.info("scrcpy 镜像流启动成功: device_id=%s, local_port=%s", self.device_id, self._local_port)

    def stop(self) -> None:
        """关闭 socket、服务端进程和 adb 转发。"""
        logger.info("开始停止 scrcpy 镜像流: %s", self.device_id)
        if self._video_stream is not None:
            self._video_stream.close()
            self._video_stream = None

        if self._video_socket is not None:
            self._video_socket.close()
            self._video_socket = None

        if self.server_process is not None:
            _terminate_process(self.server_process)
            self.server_process = None

        if self._local_port is not None:
            subprocess.run(
                self._adb_cmd("forward", "--remove", f"tcp:{self._local_port}"),
                capture_output=True,
                text=True,
                check=False,
                creationflags=_subprocess_creationflags(),
            )
            self._local_port = None

        self._server_log_drainers.clear()
        self._set_video_size(None)
        logger.info("scrcpy 镜像流已停止: %s", self.device_id)

    @property
    def stdout(self) -> BinaryIO:
        """返回 scrcpy 视频 socket 的文件对象，内容为 raw stream。"""
        if self._video_stream is None:
            raise RuntimeError("Scrcpy stream not started")
        return self._video_stream

    def read_video_chunk(self, chunk_size: int = 32768) -> bytes:
        """直接读取 scrcpy raw H.264 字节流，供 websocket 低延迟透传。"""
        if self._video_socket is None:
            raise RuntimeError("Scrcpy stream not started")
        return self._video_socket.recv(chunk_size)

    def is_running(self) -> bool:
        return self.server_process is not None and self.server_process.poll() is None

    def get_video_size(self) -> Optional[tuple[int, int]]:
        with self._video_size_lock:
            return self._video_size

    def get_or_detect_video_size(self) -> Optional[tuple[int, int]]:
        video_size = self.get_video_size()
        if video_size is not None:
            return video_size

        video_size = self._detect_device_video_size()
        if video_size is not None:
            self._set_video_size(video_size)
        return video_size

    def open_container(self) -> av.container.InputContainer:
        """使用 PyAV 打开 scrcpy 原始视频流。"""
        return av.open(self.stdout, mode="r", format=self.video_codec)

    def iter_frames(self) -> Generator[np.ndarray, None, None]:
        """持续解码视频流，返回 OpenCV 可用的 BGR 帧。"""
        container = self.open_container()
        try:
            if container.streams.video:
                stream = container.streams.video[0]
                width = getattr(stream.codec_context, "width", 0)
                height = getattr(stream.codec_context, "height", 0)
                if width and height:
                    self._set_video_size((width, height))

            for frame in container.decode(video=0):
                width = getattr(frame, "width", 0)
                height = getattr(frame, "height", 0)
                if width and height:
                    self._set_video_size((width, height))
                yield frame.to_ndarray(format="bgr24")
        finally:
            container.close()

    def preview(self, display_name: str = "Scrcpy Stream") -> None:
        """直接预览当前设备的视频流，按 q 退出。"""
        logger.info("开始本地预览镜像流: device_id=%s, display_name=%s", self.device_id, display_name)
        try:
            for frame in self.iter_frames():
                cv2.imshow(display_name, frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
        finally:
            cv2.destroyAllWindows()
            logger.info("本地预览已结束: %s", self.device_id)

    def _adb_cmd(self, *args: str) -> list[str]:
        return [_adb_binary(), "-s", self.device_id, *args]

    def _push_server(self, server_path: Path) -> None:
        logger.info("开始推送 scrcpy-server: device_id=%s, server_path=%s", self.device_id, server_path)
        result = self._run_adb_command("push", str(server_path), self._remote_server_path)
        if result.returncode != 0:
            logger.warning("推送 scrcpy-server 失败: device_id=%s", self.device_id)
            raise RuntimeError(f"推送 scrcpy-server 失败: {result.stderr.strip() or result.stdout.strip()}")
        logger.info("推送 scrcpy-server 成功: %s", self.device_id)

    def _forward_socket(self, local_port: int) -> None:
        logger.info("开始创建 adb forward: device_id=%s, local_port=%s", self.device_id, local_port)
        result = self._run_adb_command("forward", f"tcp:{local_port}", "localabstract:scrcpy")
        if result.returncode != 0:
            logger.warning("创建 adb forward 失败: device_id=%s, local_port=%s", self.device_id, local_port)
            raise RuntimeError(f"创建 scrcpy adb forward 失败: {result.stderr.strip() or result.stdout.strip()}")
        logger.info("adb forward 创建成功: device_id=%s, local_port=%s", self.device_id, local_port)

    def _run_adb_command(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            self._adb_cmd(*args),
            capture_output=True,
            text=True,
            check=False,
            creationflags=_subprocess_creationflags(),
        )
        if result.returncode == 0:
            return result

        combined_output = f"{result.stdout}\n{result.stderr}".lower()
        if _has_any(combined_output, _ADB_DAEMON_ERRORS):
            logger.info("检测到 adb server 未就绪，尝试拉起后重试: device_id=%s, args=%s", self.device_id, args)
            ensure_adb_server(allow_reset=_has_any(combined_output, _ADB_RESET_ERRORS))
            return subprocess.run(
                self._adb_cmd(*args),
                capture_output=True,
                text=True,
                check=False,
                creationflags=_subprocess_creationflags(),
            )
        return result

    def _prime_video_size(self) -> None:
        video_size = self._detect_device_video_size()
        if video_size is None:
            logger.warning("启动镜像前未能获取设备屏幕尺寸: %s", self.device_id)
            return

        self._set_video_size(video_size)
        logger.info("启动镜像前已获取设备屏幕尺寸: device_id=%s, video_size=%s", self.device_id, video_size)

    def _detect_device_video_size(self) -> Optional[tuple[int, int]]:
        result = self._run_adb_command("shell", "wm", "size")
        if result.returncode != 0:
            logger.warning(
                "获取设备屏幕尺寸失败: device_id=%s, output=%s",
                self.device_id,
                result.stderr.strip() or result.stdout.strip(),
            )
            return None

        video_size = _parse_wm_size(result.stdout)
        if video_size is None:
            return None

        orientation = self._detect_surface_orientation()
        if orientation in (1, 3):
            width, height = video_size
            return height, width
        return video_size

    def _detect_surface_orientation(self) -> Optional[int]:
        result = self._run_adb_command("shell", "dumpsys", "input")
        if result.returncode != 0:
            return None
        return _parse_surface_orientation(result.stdout)

    def _start_server_process(self) -> None:
        server_args = [
            self._server_version(),
            "tunnel_forward=true",
            "audio=false",
            "control=false",
            "cleanup=false",
            "raw_stream=true",
            f"video_codec={self.video_codec}",
            f"video_bit_rate={self.bit_rate}",
            f"max_fps={self.max_fps}",
        ]
        if self.max_size is not None and self.max_size > 0:
            server_args.append(f"max_size={self.max_size}")
        elif self.max_size is not None:
            logger.info(
                "max_size=%s，按设备原始分辨率输出 scrcpy 视频流: %s",
                self.max_size,
                self.device_id,
            )

        remote_command = " ".join(
            [
                f"CLASSPATH={shlex.quote(self._remote_server_path)}",
                "app_process / com.genymobile.scrcpy.Server",
                *[shlex.quote(arg) for arg in server_args],
            ]
        )
        logger.info("准备启动 scrcpy server: device_id=%s, command=%s", self.device_id, remote_command)
        self.server_process = subprocess.Popen(
            self._adb_cmd("shell", remote_command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=_subprocess_creationflags(),
        )

        for stream in [self.server_process.stdout, self.server_process.stderr]:
            drainer = _StreamDrainer(stream)
            drainer.start()
            self._server_log_drainers.append(drainer)

        time.sleep(0.4)
        if self.server_process.poll() is not None:
            logger.warning("scrcpy server 启动失败: %s", self.device_id)
            raise RuntimeError(f"scrcpy server 启动失败:\n{self._server_logs() or '未捕获到详细日志'}")
        logger.info("scrcpy server 进程已启动: %s", self.device_id)

    def _connect_video_socket(self, local_port: int, timeout_seconds: float = 5.0) -> None:
        deadline = time.time() + timeout_seconds
        last_error: Optional[Exception] = None

        while time.time() < deadline:
            if self.server_process is not None and self.server_process.poll() is not None:
                logger.warning("scrcpy server 在建连前已退出: %s", self.device_id)
                raise RuntimeError(f"scrcpy server 已退出:\n{self._server_logs() or '未捕获到详细日志'}")

            try:
                sock = socket.create_connection(("127.0.0.1", local_port), timeout=1.0)
                # create_connection() 会把超时保留在 socket 上，后续 PyAV 读流时
                # 会因此在暂时没有新帧时抛 TimeoutError，这里恢复为阻塞模式。
                sock.settimeout(None)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._video_socket = sock
                self._video_stream = sock.makefile("rb")
                logger.info("scrcpy 视频 socket 建连成功: device_id=%s, local_port=%s", self.device_id, local_port)
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)

        logger.warning("连接 scrcpy 视频 socket 失败: device_id=%s, local_port=%s, error=%s", self.device_id, local_port, last_error)
        raise RuntimeError(f"连接 scrcpy 视频 socket 失败: {last_error}")

    def _server_logs(self) -> str:
        return "\n".join(filter(None, (drainer.get_text() for drainer in self._server_log_drainers))).strip()

    def _set_video_size(self, video_size: Optional[tuple[int, int]]) -> None:
        with self._video_size_lock:
            self._video_size = video_size

    def _server_version(self) -> str:
        if self.server_version is None:
            self.server_version = _detect_scrcpy_version()
        return self.server_version


def _ensure_binary(binary_name: str) -> None:
    env_name = binary_name.upper()
    env_binary = os.environ.get(env_name)
    if env_binary and Path(env_binary).exists():
        return

    if shutil.which(binary_name):
        return
    logger.warning("依赖缺失，未找到可执行文件: %s", binary_name)
    raise RuntimeError(f"未找到 `{binary_name}`，请先确认它已安装并加入 PATH")


def _adb_binary() -> str:
    return os.environ.get("ADB") or "adb"


def _scrcpy_binary() -> str:
    return os.environ.get("SCRCPY") or "scrcpy"


def _subprocess_creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run_host_command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=False,
        creationflags=_subprocess_creationflags(),
    )


def _format_command_output(result: subprocess.CompletedProcess[str]) -> str:
    parts = [part.strip() for part in [result.stdout, result.stderr] if part and part.strip()]
    return "\n".join(parts)


def _probe_adb_server(adb_binary: str) -> subprocess.CompletedProcess[str]:
    return _run_host_command(adb_binary, "devices")


def ensure_adb_server(allow_reset: bool = False) -> None:
    """Ensure adb server is reachable without racing other adb clients."""
    with _ADB_SERVER_LOCK:
        _ensure_adb_server_locked(allow_reset=allow_reset)


def _ensure_adb_server_locked(allow_reset: bool = False) -> None:
    adb_binary = _adb_binary()
    logger.info("检查 adb server 状态: %s", adb_binary)

    last_start_result: subprocess.CompletedProcess[str] | None = None
    last_probe_result: subprocess.CompletedProcess[str] | None = None

    for attempt in range(1, 6):
        last_start_result = _run_host_command(adb_binary, "start-server")
        time.sleep(0.15 * attempt)
        last_probe_result = _probe_adb_server(adb_binary)

        if last_start_result.returncode == 0 and last_probe_result.returncode == 0:
            output = _format_command_output(last_start_result)
            if output:
                logger.info("adb server 已就绪: %s", output)
            return

        combined_output = _combined_command_output(last_start_result, last_probe_result).lower()
        if allow_reset or _has_any(combined_output, _ADB_RESET_ERRORS):
            logger.warning(
                "adb server 需要重置: attempt=%s, start=%s, probe=%s",
                attempt,
                _format_command_output(last_start_result) or "<empty>",
                _format_command_output(last_probe_result) or "<empty>",
            )
            _reset_adb_server_locked(adb_binary)
            return

        logger.info(
            "adb server 暂未就绪，等待后重试: attempt=%s, start=%s, probe=%s",
            attempt,
            _format_command_output(last_start_result) or "<empty>",
            _format_command_output(last_probe_result) or "<empty>",
        )

    logger.warning(
        "adb server 多次启动或校验失败: start=%s, probe=%s",
        _format_command_output(last_start_result) if last_start_result else "<empty>",
        _format_command_output(last_probe_result) if last_probe_result else "<empty>",
    )

    error_message = (
        "启动 adb server 失败:\n"
        f"start-server:\n{_format_command_output(last_start_result) if last_start_result else '<empty>'}\n"
        f"devices:\n{_format_command_output(last_probe_result) if last_probe_result else '<empty>'}\n"
        "请检查 5037 端口是否被其它 adb 或非 adb 进程占用，以及当前配置的 adb 是否可单独正常执行"
    )
    logger.warning(error_message)
    raise RuntimeError(error_message)


def _reset_adb_server_locked(adb_binary: str) -> None:
    _run_host_command(adb_binary, "kill-server")
    time.sleep(0.5)

    retry_start_result = _run_host_command(adb_binary, "start-server")
    retry_probe_result = _probe_adb_server(adb_binary)
    if retry_start_result.returncode == 0 and retry_probe_result.returncode == 0:
        output = _format_command_output(retry_start_result)
        if output:
            logger.info("adb server 重置后已恢复: %s", output)
        return

    error_message = (
        "启动 adb server 失败:\n"
        f"start-server:\n{_format_command_output(retry_start_result) or '<empty>'}\n"
        f"devices:\n{_format_command_output(retry_probe_result) or '<empty>'}\n"
        "请检查 5037 端口是否被其它 adb 或非 adb 进程占用，以及当前配置的 adb 是否可单独正常执行"
    )
    logger.warning(error_message)
    raise RuntimeError(error_message)


def _combined_command_output(*results: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(_format_command_output(result) for result in results if result is not None)


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _parse_wm_size(output: str) -> Optional[tuple[int, int]]:
    preferred_labels = ("Override size", "Physical size")
    for label in preferred_labels:
        match = re.search(rf"{re.escape(label)}:\s*(\d+)x(\d+)", output)
        if match:
            return int(match.group(1)), int(match.group(2))

    match = re.search(r"(\d+)x(\d+)", output)
    if match:
        return int(match.group(1)), int(match.group(2))

    logger.warning("无法解析 wm size 输出: %s", output.strip())
    return None


def _parse_surface_orientation(output: str) -> Optional[int]:
    match = re.search(r"SurfaceOrientation:\s*(\d+)", output)
    if not match:
        return None
    return int(match.group(1))


def _detect_scrcpy_version() -> str:
    result = subprocess.run(
        [_scrcpy_binary(), "--version"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=_subprocess_creationflags(),
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("无法获取 scrcpy 版本信息")

    first_line = result.stdout.splitlines()[0].strip()
    parts = first_line.split()
    if len(parts) < 2:
        raise RuntimeError(f"无法解析 scrcpy 版本信息: {first_line}")
    return parts[1]


def _detect_first_device_id() -> str:
    ensure_adb_server()
    result = subprocess.run(
        [_adb_binary(), "devices"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=_subprocess_creationflags(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"执行 `adb devices` 失败: {result.stderr.strip() or result.stdout.strip()}")

    device_ids: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices attached"):
            continue

        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            device_ids.append(parts[0])

    if not device_ids:
        logger.warning("未检测到可用安卓设备")
        raise RuntimeError("未检测到可用安卓设备，请先连接手机并开启 USB 调试")

    logger.info("自动选中第一台在线设备: %s", device_ids[0])
    return device_ids[0]


def _resolve_scrcpy_server_path() -> Path:
    configured_scrcpy_binary = os.environ.get("SCRCPY")
    scrcpy_binary = configured_scrcpy_binary if configured_scrcpy_binary and Path(configured_scrcpy_binary).exists() else shutil.which("scrcpy")
    if not scrcpy_binary:
        raise RuntimeError("未找到 scrcpy，请先确认它已安装并加入 PATH")

    env_server_path = os.environ.get("SCRCPY_SERVER_PATH")
    if env_server_path:
        candidate = Path(env_server_path).expanduser().resolve()
        if candidate.exists():
            logger.info("使用环境变量指定的 scrcpy-server 路径: %s", candidate)
            return candidate
        logger.warning("SCRCPY_SERVER_PATH 指向的文件不存在: %s", candidate)

    resolved_binary = Path(scrcpy_binary).resolve()
    candidate_paths = [
        resolved_binary.parent / "scrcpy-server",
        resolved_binary.parent / "scrcpy-server.jar",
        resolved_binary.parent.parent / "share" / "scrcpy" / "scrcpy-server",
        resolved_binary.parent.parent / "share" / "scrcpy" / "scrcpy-server.jar",
    ]

    for candidate in candidate_paths:
        if candidate.exists():
            logger.info("检测到 scrcpy-server 路径: %s", candidate)
            return candidate

    logger.warning("未找到 scrcpy-server 文件")
    raise RuntimeError(
        "未找到 scrcpy-server 文件，请检查 scrcpy 安装目录；Windows 下通常应与 scrcpy.exe 位于同一目录，"
        "也可通过环境变量 SCRCPY_SERVER_PATH 显式指定"
    )


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于 scrcpy standalone server 的安卓屏幕镜像验证")
    parser.add_argument("--device-id", default=None, help="adb 设备序列号，不传则自动取第一台在线设备")
    parser.add_argument("--bit-rate", type=int, default=4_000_000, help="视频码率，默认 4000000")
    parser.add_argument("--max-fps", type=int, default=30, help="最大帧率，默认 30")
    parser.add_argument("--max-size", type=int, default=0, help="最大边长限制，默认 0 表示原始分辨率")
    parser.add_argument(
        "--video-codec",
        choices=["h264", "h265", "av1"],
        default="h264",
        help="视频编码格式，默认 h264",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    device_id = args.device_id or _detect_first_device_id()
    streamer = ScrcpyStreamer(
        device_id=device_id,
        max_fps=args.max_fps,
        bit_rate=args.bit_rate,
        max_size=args.max_size,
        video_codec=args.video_codec,
    )
    streamer.start()
    print(f"scrcpy 视频流已启动，当前设备: {device_id}，按 q 退出预览")

    try:
        while not streamer.is_running():
            time.sleep(1)
        streamer.preview()
    finally:
        streamer.stop()


if __name__ == "__main__":
    main()
