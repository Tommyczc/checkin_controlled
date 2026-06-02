from __future__ import annotations

import argparse
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
        bit_rate: int = 8_000_000,
        max_size: Optional[int] = None,
        video_codec: str = "h264",
        server_version: Optional[str] = None,
    ):
        self.device_id = device_id
        self.max_fps = max_fps
        self.bit_rate = bit_rate
        self.max_size = max_size
        self.video_codec = video_codec
        self.server_version = server_version or _detect_scrcpy_version()

        self.server_process: Optional[subprocess.Popen] = None
        self._server_log_drainers: list[_StreamDrainer] = []
        self._video_socket: Optional[socket.socket] = None
        self._video_stream: Optional[BinaryIO] = None
        self._local_port: Optional[int] = None
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

        logger.info("开始启动 scrcpy 镜像流: %s", self.device_id)
        _ensure_binary("adb")
        _ensure_binary("scrcpy")

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
            )
            self._local_port = None

        self._server_log_drainers.clear()
        logger.info("scrcpy 镜像流已停止: %s", self.device_id)

    @property
    def stdout(self) -> BinaryIO:
        """返回 scrcpy 视频 socket 的文件对象，内容为 raw stream。"""
        if self._video_stream is None:
            raise RuntimeError("Scrcpy stream not started")
        return self._video_stream

    def is_running(self) -> bool:
        return self.server_process is not None and self.server_process.poll() is None

    def open_container(self) -> av.container.InputContainer:
        """使用 PyAV 打开 scrcpy 原始视频流。"""
        return av.open(self.stdout, mode="r", format=self.video_codec)

    def iter_frames(self) -> Generator[np.ndarray, None, None]:
        """持续解码视频流，返回 OpenCV 可用的 BGR 帧。"""
        container = self.open_container()
        try:
            for frame in container.decode(video=0):
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
        return ["adb", "-s", self.device_id, *args]

    def _push_server(self, server_path: Path) -> None:
        logger.info("开始推送 scrcpy-server: device_id=%s, server_path=%s", self.device_id, server_path)
        result = subprocess.run(
            self._adb_cmd("push", str(server_path), self._remote_server_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("推送 scrcpy-server 失败: device_id=%s", self.device_id)
            raise RuntimeError(f"推送 scrcpy-server 失败: {result.stderr.strip() or result.stdout.strip()}")
        logger.info("推送 scrcpy-server 成功: %s", self.device_id)

    def _forward_socket(self, local_port: int) -> None:
        logger.info("开始创建 adb forward: device_id=%s, local_port=%s", self.device_id, local_port)
        result = subprocess.run(
            self._adb_cmd("forward", f"tcp:{local_port}", "localabstract:scrcpy"),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("创建 adb forward 失败: device_id=%s, local_port=%s", self.device_id, local_port)
            raise RuntimeError(f"创建 scrcpy adb forward 失败: {result.stderr.strip() or result.stdout.strip()}")
        logger.info("adb forward 创建成功: device_id=%s, local_port=%s", self.device_id, local_port)

    def _start_server_process(self) -> None:
        server_args = [
            self.server_version,
            "tunnel_forward=true",
            "audio=false",
            "control=false",
            "cleanup=false",
            "raw_stream=true",
            f"video_codec={self.video_codec}",
            f"video_bit_rate={self.bit_rate}",
            f"max_fps={self.max_fps}",
        ]
        if self.max_size is not None:
            server_args.append(f"max_size={self.max_size}")

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


def _ensure_binary(binary_name: str) -> None:
    if shutil.which(binary_name):
        return
    logger.warning("依赖缺失，未找到可执行文件: %s", binary_name)
    raise RuntimeError(f"未找到 `{binary_name}`，请先确认它已安装并加入 PATH")


def _detect_scrcpy_version() -> str:
    result = subprocess.run(
        ["scrcpy", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("无法获取 scrcpy 版本信息")

    first_line = result.stdout.splitlines()[0].strip()
    parts = first_line.split()
    if len(parts) < 2:
        raise RuntimeError(f"无法解析 scrcpy 版本信息: {first_line}")
    return parts[1]


def _detect_first_device_id() -> str:
    result = subprocess.run(
        ["adb", "devices"],
        capture_output=True,
        text=True,
        check=False,
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
    scrcpy_binary = shutil.which("scrcpy")
    if not scrcpy_binary:
        raise RuntimeError("未找到 scrcpy，可先执行 `brew install scrcpy`")

    resolved_binary = Path(scrcpy_binary).resolve()
    candidate_paths = [
        resolved_binary.parent.parent / "share" / "scrcpy" / "scrcpy-server",
        resolved_binary.parent.parent / "share" / "scrcpy" / "scrcpy-server.jar",
    ]

    for candidate in candidate_paths:
        if candidate.exists():
            logger.info("检测到 scrcpy-server 路径: %s", candidate)
            return candidate

    logger.warning("未找到 scrcpy-server 文件")
    raise RuntimeError("未找到 scrcpy-server 文件，请检查 scrcpy 安装是否完整")


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
    parser.add_argument("--bit-rate", type=int, default=8_000_000, help="视频码率，默认 8000000")
    parser.add_argument("--max-fps", type=int, default=30, help="最大帧率，默认 30")
    parser.add_argument("--max-size", type=int, default=None, help="最大边长限制，例如 1600")
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
