from __future__ import annotations

import os
import platform
import re
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from typing import BinaryIO, Optional

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


class IosMirrorStreamer:
    """macOS 使用 ReplayKit USB raw H.264；Windows 使用 WDA MJPEG。"""

    def __init__(
        self,
        device_id: str,
        udid: Optional[str] = None,
        device_name: str = "",
        max_fps: int = 30,
        bit_rate: int = 4_000_000,
        max_size: Optional[int] = 0,
        video_codec: str = "h264",
        mjpeg_url: Optional[str] = None,
        wda_url: Optional[str] = None,
        wda_port: int = 8100,
        mjpeg_port: int = 9100,
        replaykit_port: int = 27777,
        device_wda_port: int = 8100,
        device_mjpeg_port: int = 9100,
        device_replaykit_port: int = 27777,
        local_wda_port: Optional[int] = None,
        local_mjpeg_port: Optional[int] = None,
        local_replaykit_port: Optional[int] = None,
    ):
        self.device_id = device_id
        self.udid = udid or device_id
        self.device_name = device_name
        self.max_fps = max_fps
        self.bit_rate = bit_rate
        self.max_size = max_size
        self.video_codec = video_codec
        self.mjpeg_url = mjpeg_url
        self.wda_url = wda_url
        self.local_wda_port = local_wda_port if local_wda_port is not None else wda_port
        self.local_mjpeg_port = local_mjpeg_port if local_mjpeg_port is not None else mjpeg_port
        self.local_replaykit_port = local_replaykit_port if local_replaykit_port is not None else replaykit_port
        self.device_wda_port = device_wda_port
        self.device_mjpeg_port = device_mjpeg_port
        self.device_replaykit_port = device_replaykit_port
        self.wda_port = self.local_wda_port
        self.mjpeg_port = self.local_mjpeg_port
        self.replaykit_port = self.local_replaykit_port

        self.ffmpeg_process: Optional[subprocess.Popen] = None
        self._video_socket: Optional[socket.socket] = None
        self._forward_processes: list[subprocess.Popen] = []
        self._stderr_drainer: Optional[_StreamDrainer] = None
        self._video_size: Optional[tuple[int, int]] = None
        self._capture_backend: Optional[str] = None
        self._avfoundation_device_name: Optional[str] = None
        self._mjpeg_stream_url: Optional[str] = None
        self._last_restart_at = 0.0
        self._restart_count = 0
        logger.info(
            "IosMirrorStreamer 初始化完成: device_id=%s, udid=%s, device_name=%s, max_fps=%s, bit_rate=%s, max_size=%s, video_codec=%s, local_wda_port=%s, local_mjpeg_port=%s, local_replaykit_port=%s, device_wda_port=%s, device_mjpeg_port=%s, device_replaykit_port=%s",
            device_id,
            self.udid,
            self.device_name,
            max_fps,
            bit_rate,
            max_size,
            video_codec,
            self.local_wda_port,
            self.local_mjpeg_port,
            self.local_replaykit_port,
            self.device_wda_port,
            self.device_mjpeg_port,
            self.device_replaykit_port,
        )

    def start(self) -> None:
        """启动当前平台的采集后端，输出 router 已支持的 raw H.264。"""
        if self.ffmpeg_process is not None or self._video_socket is not None:
            raise RuntimeError("iOS mirror stream already running")

        if self.video_codec != "h264":
            raise RuntimeError("iOS 镜像当前仅支持输出 h264")

        self._capture_backend = _capture_backend_for_platform()
        force_backend = _capture_backend_override()
        if force_backend:
            self._capture_backend = force_backend
        self._ensure_backend_allowed()

        if self._capture_backend == "replaykit_usb":
            self._start_replaykit_usb_backend()
            return

        _ensure_binary("ffmpeg")
        if self._capture_backend == "avfoundation":
            try:
                self._start_avfoundation_backend()
                return
            except Exception as exc:
                if force_backend == "avfoundation":
                    raise
                logger.warning(
                    "iOS AVFoundation 屏幕采集不可用，自动降级为 WDA MJPEG: device_id=%s, error=%s",
                    self.device_id,
                    exc,
                )
                if self.ffmpeg_process is not None:
                    _terminate_process(self.ffmpeg_process)
                    self.ffmpeg_process = None
                self._stderr_drainer = None
                self._avfoundation_device_name = None
                self._capture_backend = "wda_mjpeg"

        self._start_wda_mjpeg_backend()

    def _start_replaykit_usb_backend(self) -> None:
        self._capture_backend = "replaykit_usb"
        self._ensure_replaykit_forward()
        self._connect_replaykit_stream()
        logger.info(
            "iOS ReplayKit USB 镜像流启动成功: device_id=%s, local_port=%s, device_port=%s",
            self.device_id,
            self.local_replaykit_port,
            self.device_replaykit_port,
        )

    def _start_avfoundation_backend(self) -> None:
        avfoundation_device = self._resolve_avfoundation_video_device()
        self._avfoundation_device_name = avfoundation_device[1]
        self._start_ffmpeg_process(self._build_avfoundation_ffmpeg_command(avfoundation_device[0]))
        logger.info(
            "iOS AVFoundation 镜像流启动成功: device_id=%s, input_index=%s, input_name=%s",
            self.device_id,
            avfoundation_device[0],
            avfoundation_device[1],
        )

    def _start_wda_mjpeg_backend(self) -> None:
        self._capture_backend = "wda_mjpeg"
        self._ensure_local_forwards()
        url = self._get_mjpeg_url()
        self._probe_mjpeg_url(url)

        command = self._build_mjpeg_ffmpeg_command(url)
        self._mjpeg_stream_url = url
        self._start_ffmpeg_process(command)

        logger.info(
            "iOS WDA MJPEG 镜像转码流启动成功: device_id=%s, mjpeg_url=%s",
            self.device_id,
            url,
        )

    def _fallback_to_wda_mjpeg_backend(self, reason: Exception) -> None:
        logger.warning(
            "iOS AVFoundation 镜像重启失败，自动降级为 WDA MJPEG: device_id=%s, error=%s",
            self.device_id,
            reason,
        )
        self._capture_backend = "wda_mjpeg"
        self._avfoundation_device_name = None
        self._start_wda_mjpeg_backend()

    def _ensure_backend_allowed(self) -> None:
        current_system = platform.system()
        if self._capture_backend == "replaykit_usb" and current_system != "Darwin":
            raise RuntimeError("ReplayKit USB 镜像后端仅支持 macOS")
        if self._capture_backend == "avfoundation":
            raise RuntimeError("macOS iOS 镜像已切换为 ReplayKit USB，不再使用 AVFoundation")
        if self._capture_backend == "wda_mjpeg" and current_system != "Windows":
            raise RuntimeError(f"WDA MJPEG 镜像后端仅支持 Windows，当前平台: {current_system or '未知'}")

    def _restart_avfoundation_backend(self) -> list[str]:
        try:
            avfoundation_device = self._resolve_avfoundation_video_device()
            self._avfoundation_device_name = avfoundation_device[1]
            return self._build_avfoundation_ffmpeg_command(avfoundation_device[0])
        except Exception as exc:
            if _capture_backend_override() == "avfoundation":
                raise
            self._fallback_to_wda_mjpeg_backend(exc)
            return []

    def _restart_wda_mjpeg_backend(self) -> list[str]:
        if self._mjpeg_stream_url is None:
            raise RuntimeError("iOS mirror stream url not initialized")
        self._probe_mjpeg_url(self._mjpeg_stream_url)
        return self._build_mjpeg_ffmpeg_command(self._mjpeg_stream_url)

    def stop(self) -> None:
        logger.info("开始停止 iOS 镜像转码流: %s", self.device_id)
        if self.ffmpeg_process is not None:
            _terminate_process(self.ffmpeg_process)
            self.ffmpeg_process = None
        self._close_video_socket()

        self._stop_local_forwards()
        self._stderr_drainer = None
        self._video_size = None
        self._capture_backend = None
        self._avfoundation_device_name = None
        self._mjpeg_stream_url = None
        logger.info("iOS 镜像转码流已停止: %s", self.device_id)

    @property
    def stdout(self) -> BinaryIO:
        if self.ffmpeg_process is None or self.ffmpeg_process.stdout is None:
            raise RuntimeError("iOS mirror stream not started")
        return self.ffmpeg_process.stdout

    def read_video_chunk(self, chunk_size: int = 32768) -> bytes:
        if self._capture_backend == "replaykit_usb":
            return self._read_replaykit_chunk(chunk_size)

        if self.ffmpeg_process is None or self.ffmpeg_process.stdout is None:
            raise RuntimeError("iOS mirror stream not started")

        try:
            chunk = self._read_video_chunk_once(chunk_size)
        except TimeoutError:
            logger.warning("iOS 镜像转码流读取超时，准备重启 ffmpeg: device_id=%s", self.device_id)
            self._restart_ffmpeg_process()
            return self._read_video_chunk_once(chunk_size)

        if chunk or self.ffmpeg_process is None or self.ffmpeg_process.poll() is None:
            return chunk

        logger.warning(
            "iOS 镜像转码流 stdout 已关闭，准备重启 ffmpeg: device_id=%s, stderr=%s",
            self.device_id,
            self._stderr_logs() or "无 ffmpeg stderr",
        )
        self._restart_ffmpeg_process()
        return self._read_video_chunk_once(chunk_size)

    def is_running(self) -> bool:
        if self._capture_backend == "replaykit_usb":
            return self._video_socket is not None and self._video_socket.fileno() >= 0
        return self.ffmpeg_process is not None and self.ffmpeg_process.poll() is None

    def get_video_size(self) -> Optional[tuple[int, int]]:
        return self._video_size

    def get_or_detect_video_size(self) -> Optional[tuple[int, int]]:
        return self.get_video_size()

    def requires_wda_for_mirror(self) -> bool:
        backend = _capture_backend_override() or _capture_backend_for_platform()
        return backend == "wda_mjpeg"

    def _get_mjpeg_url(self) -> str:
        if self.mjpeg_url:
            return self.mjpeg_url
        return f"http://127.0.0.1:{self.local_mjpeg_port}"

    def _get_wda_status_url(self) -> str:
        if self.wda_url:
            return f"{self.wda_url.rstrip('/')}/status"
        return f"http://127.0.0.1:{self.local_wda_port}/status"

    def _ensure_local_forwards(self) -> None:
        if self.mjpeg_url and self.wda_url:
            return

        if not self.mjpeg_url:
            self._ensure_local_forward(local_port=self.local_mjpeg_port, remote_port=self.device_mjpeg_port, label="MJPEG")
        if not self.wda_url:
            self._ensure_local_forward(local_port=self.local_wda_port, remote_port=self.device_wda_port, label="WDA")

    def _ensure_replaykit_forward(self) -> None:
        if _is_tcp_port_open("127.0.0.1", self.local_replaykit_port):
            logger.info(
                "iOS ReplayKit 本机端口已可连接，复用现有转发: device_id=%s, port=%s",
                self.device_id,
                self.local_replaykit_port,
            )
            return

        if shutil.which("iproxy"):
            self._start_iproxy_forward(
                local_port=self.local_replaykit_port,
                remote_port=self.device_replaykit_port,
                label="ReplayKit",
            )
            return

        self._ensure_local_forward(
            local_port=self.local_replaykit_port,
            remote_port=self.device_replaykit_port,
            label="ReplayKit",
        )

    def _start_iproxy_forward(self, local_port: int, remote_port: int, label: str) -> None:
        command = [
            "iproxy",
            "-u",
            self.udid,
            "-s",
            "127.0.0.1",
            f"{local_port}:{remote_port}",
        ]
        logger.info(
            "开始自动转发 iOS %s 端口: device_id=%s, local_port=%s, remote_port=%s, command=%s",
            label,
            self.device_id,
            local_port,
            remote_port,
            command,
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_subprocess_creationflags(),
        )
        self._forward_processes.append(process)
        self._wait_forward_ready(process, local_port, label)

    def _ensure_local_forward(self, local_port: int, remote_port: int, label: str) -> None:
        if _is_tcp_port_open("127.0.0.1", local_port):
            logger.info("iOS %s 本机端口已可连接，复用现有转发: device_id=%s, port=%s", label, self.device_id, local_port)
            return

        try:
            import pymobiledevice3  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                f"无法自动转发 iOS {label} 端口，当前 Python 环境未安装 pymobiledevice3。"
                f"请安装依赖，或手动执行 `pymobiledevice3 usbmux forward {local_port} {remote_port} --serial {self.udid}`"
            ) from exc

        command = [
            sys.executable,
            "-m",
            "pymobiledevice3",
            "usbmux",
            "forward",
            str(local_port),
            str(remote_port),
            "--serial",
            self.udid,
            "--host",
            "127.0.0.1",
        ]
        logger.info(
            "开始自动转发 iOS %s 端口: device_id=%s, local_port=%s, remote_port=%s, command=%s",
            label,
            self.device_id,
            local_port,
            remote_port,
            command,
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_subprocess_creationflags(),
        )
        self._forward_processes.append(process)
        self._wait_forward_ready(process, local_port, label)

    def _wait_forward_ready(self, process: subprocess.Popen, local_port: int, label: str, timeout_seconds: float = 3.0) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=0.2)
                raise RuntimeError(
                    f"iOS {label} 端口自动转发进程已退出: local_port={local_port}, "
                    f"stdout={stdout.strip()}, stderr={stderr.strip()}"
                )
            if _is_tcp_port_open("127.0.0.1", local_port):
                logger.info("iOS %s 端口自动转发已就绪: device_id=%s, local_port=%s", label, self.device_id, local_port)
                return
            time.sleep(0.1)

        raise RuntimeError(f"iOS {label} 端口自动转发超时: local_port={local_port}")

    def _stop_local_forwards(self) -> None:
        for process in self._forward_processes:
            _terminate_process(process)
        self._forward_processes.clear()

    def _probe_mjpeg_url(self, url: str) -> None:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "checkin-server/ios-mirror"})
            with urllib.request.urlopen(request, timeout=3) as response:
                content_type = response.headers.get("Content-Type", "")
                logger.info("iOS WDA MJPEG 地址可访问: device_id=%s, url=%s, content_type=%s", self.device_id, url, content_type)
                try:
                    video_size = _read_mjpeg_stream_size(response)
                except Exception as size_exc:
                    logger.info("读取 iOS WDA MJPEG 首帧尺寸失败，继续启动镜像: device_id=%s, error=%s", self.device_id, size_exc)
                    video_size = None
                if video_size is not None:
                    self._video_size = video_size
                    logger.info("iOS WDA MJPEG 首帧尺寸: device_id=%s, video_size=%s", self.device_id, video_size)
        except Exception as exc:
            status_url = self._get_wda_status_url()
            status_error = self._probe_wda_status_error(status_url)
            if status_error:
                raise RuntimeError(
                    "无法访问 iOS WDA MJPEG 流，且 WDA 状态口也不可访问。"
                    "请通过 xctest/runwda 启动 WDA，并把设备端口转发到该设备分配到的本机端口。"
                    f"本机 WDA 端口: {self.local_wda_port}->设备 {self.device_wda_port}; "
                    f"本机 MJPEG 端口: {self.local_mjpeg_port}->设备 {self.device_mjpeg_port}; "
                    f"MJPEG 地址: {url}, MJPEG error={exc}; WDA 状态地址: {status_url}, status error={status_error}"
                ) from exc

            raise RuntimeError(
                "WDA 状态口可访问，但 MJPEG 流不可访问。"
                f"请确认本机 MJPEG 端口 {self.local_mjpeg_port} 已转发到设备端口 {self.device_mjpeg_port}，"
                "或 WDA 启动时启用了 MJPEG 服务。"
                f"MJPEG 地址: {url}, error={exc}; WDA 状态地址: {status_url}"
            ) from exc

    def _probe_wda_status_error(self, status_url: str) -> Optional[Exception]:
        try:
            request = urllib.request.Request(status_url, headers={"User-Agent": "checkin-server/ios-mirror"})
            with urllib.request.urlopen(request, timeout=3) as response:
                logger.info(
                    "iOS WDA 状态口可访问: device_id=%s, url=%s, status=%s",
                    self.device_id,
                    status_url,
                    response.status,
                )
            return None
        except Exception as exc:
            return exc

    def _resolve_avfoundation_video_device(self) -> tuple[int, str]:
        devices = _list_avfoundation_video_devices()
        logger.info("AVFoundation 视频设备列表: device_id=%s, devices=%s", self.device_id, devices)
        if not devices:
            raise RuntimeError(
                "AVFoundation 未枚举到任何视频设备，请确认 ffmpeg 具备 avfoundation 支持，"
                "并允许终端/Python 访问相机与屏幕录制权限"
            )

        target_name = _normalize_avfoundation_name(self.device_name)
        if not target_name:
            raise RuntimeError(
                f"无法为 iOS 设备 {self.udid} 匹配 AVFoundation 输入：设备名称为空。"
                f"当前视频设备: {_format_avfoundation_devices(devices)}"
            )

        exact_matches = [device for device in devices if _normalize_avfoundation_name(device[1]) == target_name]
        matches = exact_matches or [
            device
            for device in devices
            if target_name in _normalize_avfoundation_name(device[1])
            and not _looks_like_camera_or_desktop_input(device[1])
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                f"AVFoundation 找到多个与 iOS 设备名称 {self.device_name!r} 匹配的输入，"
                f"无法根据 UDID 区分: {_format_avfoundation_devices(matches)}"
            )

        raise RuntimeError(
            f"AVFoundation 未找到 iOS 设备 {self.device_name!r} 的屏幕采集输入。"
            "已排除连续互通相机、桌上视角和 Mac 屏幕，避免误投摄像头画面。"
            f"当前视频设备: {_format_avfoundation_devices(devices)}"
        )

    def _build_avfoundation_ffmpeg_command(self, device_index: int) -> list[str]:
        fps = max(1, int(self.max_fps or 30))
        bit_rate = max(1, int(self.bit_rate or 4_000_000))
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-thread_queue_size",
            "2",
            "-f",
            "avfoundation",
            "-framerate",
            str(fps),
            "-pixel_format",
            "nv12",
            "-i",
            f"{device_index}:none",
            "-an",
            "-c:v",
            "h264_videotoolbox",
            "-realtime",
            "1",
            "-prio_speed",
            "1",
            "-allow_sw",
            "1",
            "-profile:v",
            "baseline",
            "-pix_fmt",
            "yuv420p",
            "-bf",
            "0",
            "-r",
            str(fps),
            "-g",
            str(fps),
            "-b:v",
            str(bit_rate),
            "-f",
            "h264",
            "pipe:1",
        ]

    def _build_mjpeg_ffmpeg_command(self, url: str) -> list[str]:
        fps = max(1, int(self.max_fps or 30))
        bit_rate = max(1, int(self.bit_rate or 4_000_000))
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_at_eof",
            "1",
            "-reconnect_delay_max",
            "1",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-probesize",
            "4096",
            "-analyzeduration",
            "100000",
            "-f",
            "mpjpeg",
            "-strict_mime_boundary",
            "0",
            "-i",
            url,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-profile:v",
            "baseline",
            "-pix_fmt",
            "yuv420p",
            "-bf",
            "0",
            "-r",
            str(fps),
            "-g",
            str(fps),
            "-b:v",
            str(bit_rate),
            "-f",
            "h264",
            "pipe:1",
        ]

    def _stderr_logs(self) -> str:
        if self._stderr_drainer is None:
            return ""
        return self._stderr_drainer.get_text()

    def _start_ffmpeg_process(self, command: list[str]) -> None:
        logger.info("开始启动 iOS 镜像转码流: device_id=%s, command=%s", self.device_id, command)
        self.ffmpeg_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=_subprocess_creationflags(),
        )
        self._stderr_drainer = _StreamDrainer(self.ffmpeg_process.stderr)
        self._stderr_drainer.start()

        time.sleep(0.4)
        if self.ffmpeg_process.poll() is not None:
            error = self._stderr_logs() or "未捕获到详细日志"
            self.ffmpeg_process = None
            raise RuntimeError(f"iOS 镜像转码流启动失败:\n{error}")

    def _read_video_chunk_once(self, chunk_size: int, timeout_seconds: float = 8.0) -> bytes:
        if self.ffmpeg_process is None or self.ffmpeg_process.stdout is None:
            raise RuntimeError("iOS mirror stream not started")

        if os.name != "nt":
            fd = self.ffmpeg_process.stdout.fileno()
            readable, _, _ = select.select([fd], [], [], timeout_seconds)
            if not readable:
                raise TimeoutError(f"iOS mirror stream read timeout after {timeout_seconds}s")
            return os.read(fd, chunk_size)

        return os.read(self.ffmpeg_process.stdout.fileno(), chunk_size)

    def _read_replaykit_chunk(self, chunk_size: int) -> bytes:
        try:
            chunk = self._read_replaykit_chunk_once(chunk_size)
        except TimeoutError:
            logger.warning(
                "iOS ReplayKit USB 镜像流读取超时，准备重连 TCP: device_id=%s, local_port=%s",
                self.device_id,
                self.local_replaykit_port,
            )
            self._restart_replaykit_stream()
            return self._read_replaykit_chunk_once(chunk_size)
        except OSError as exc:
            logger.warning(
                "iOS ReplayKit USB 镜像流读取失败，准备重连 TCP: device_id=%s, error=%s",
                self.device_id,
                exc,
            )
            self._restart_replaykit_stream()
            return self._read_replaykit_chunk_once(chunk_size)

        if chunk:
            return chunk

        logger.warning("iOS ReplayKit USB 镜像流连接已关闭，准备重连 TCP: device_id=%s", self.device_id)
        self._restart_replaykit_stream()
        return self._read_replaykit_chunk_once(chunk_size)

    def _read_replaykit_chunk_once(self, chunk_size: int, timeout_seconds: float = 8.0) -> bytes:
        if self._video_socket is None:
            raise RuntimeError("iOS ReplayKit USB mirror stream not connected")

        fd = self._video_socket.fileno()
        if fd < 0:
            raise RuntimeError("iOS ReplayKit USB mirror stream socket is closed")

        readable, _, _ = select.select([fd], [], [], timeout_seconds)
        if not readable:
            raise TimeoutError(f"iOS ReplayKit USB mirror stream read timeout after {timeout_seconds}s")
        return self._video_socket.recv(chunk_size)

    def _connect_replaykit_stream(self, timeout_seconds: float = 8.0) -> None:
        self._close_video_socket()
        deadline = time.time() + timeout_seconds
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                self._video_socket = socket.create_connection(("127.0.0.1", self.local_replaykit_port), timeout=1.0)
                self._video_socket.setblocking(True)
                logger.info(
                    "iOS ReplayKit USB TCP 已连接: device_id=%s, local_port=%s",
                    self.device_id,
                    self.local_replaykit_port,
                )
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)

        raise RuntimeError(
            "无法连接 iOS ReplayKit USB H.264 流。"
            f"请确认 ReplayUSB Broadcast 已在 iPhone 上开始广播，并已绑定端口 "
            f"{self.local_replaykit_port}->{self.device_replaykit_port}。"
            f"device_id={self.device_id}, udid={self.udid}, last_error={last_error}"
        )

    def _restart_replaykit_stream(self) -> None:
        self._ensure_restart_budget()
        self._close_video_socket()
        self._ensure_replaykit_forward()
        self._connect_replaykit_stream()
        logger.info(
            "iOS ReplayKit USB 镜像流已重连: device_id=%s, restart_count=%s",
            self.device_id,
            self._restart_count,
        )

    def _close_video_socket(self) -> None:
        if self._video_socket is None:
            return
        try:
            self._video_socket.close()
        finally:
            self._video_socket = None

    def _ensure_restart_budget(self) -> None:
        now = time.monotonic()
        if now - self._last_restart_at > 60:
            self._restart_count = 0
        self._last_restart_at = now
        self._restart_count += 1
        if self._restart_count > 6:
            raise RuntimeError(
                f"iOS 镜像流一分钟内重连/重启次数过多，请检查 {self._capture_backend} 采集流稳定性"
            )

    def _restart_ffmpeg_process(self) -> None:
        if self._capture_backend is None:
            raise RuntimeError("iOS mirror capture backend not initialized")

        self._ensure_restart_budget()

        if self.ffmpeg_process is not None:
            _terminate_process(self.ffmpeg_process)
            self.ffmpeg_process = None

        if self._capture_backend == "avfoundation":
            command = self._restart_avfoundation_backend()
            if not command:
                logger.info(
                    "iOS 镜像转码流已重启: device_id=%s, backend=%s, restart_count=%s",
                    self.device_id,
                    self._capture_backend,
                    self._restart_count,
                )
                return
        else:
            command = self._restart_wda_mjpeg_backend()

        self._start_ffmpeg_process(command)
        logger.info(
            "iOS 镜像转码流已重启: device_id=%s, backend=%s, restart_count=%s",
            self.device_id,
            self._capture_backend,
            self._restart_count,
        )


def _ensure_binary(binary_name: str) -> None:
    if shutil.which(binary_name):
        return
    logger.warning("依赖缺失，未找到可执行文件: %s", binary_name)
    raise RuntimeError(f"未找到 `{binary_name}`，请先确认它已安装并加入 PATH")


def _capture_backend_for_platform(system_name: Optional[str] = None) -> str:
    current_system = system_name or platform.system()
    if current_system == "Darwin":
        return "replaykit_usb"
    if current_system == "Windows":
        return "wda_mjpeg"
    raise RuntimeError(f"iOS 镜像仅支持 macOS 和 Windows，当前平台: {current_system or '未知'}")


def _capture_backend_override() -> Optional[str]:
    raw_value = os.environ.get("IOS_MIRROR_BACKEND", "").strip().casefold().replace("-", "_")
    if not raw_value or raw_value == "auto":
        return None

    aliases = {
        "replaykit": "replaykit_usb",
        "replaykit_usb": "replaykit_usb",
        "usb": "replaykit_usb",
        "mjpeg": "wda_mjpeg",
        "wda": "wda_mjpeg",
        "wda_mjpeg": "wda_mjpeg",
    }
    backend = aliases.get(raw_value)
    if backend is None:
        raise RuntimeError("IOS_MIRROR_BACKEND 仅支持 auto、replaykit_usb、wda_mjpeg")
    return backend


def _list_avfoundation_video_devices() -> list[tuple[int, str]]:
    command = ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            creationflags=_subprocess_creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("AVFoundation 视频设备枚举超时") from exc

    output = "\n".join(part for part in [result.stdout, result.stderr] if part)
    devices: list[tuple[int, str]] = []
    in_video_devices = False
    for line in output.splitlines():
        if "AVFoundation video devices:" in line:
            in_video_devices = True
            continue
        if "AVFoundation audio devices:" in line:
            in_video_devices = False
            continue
        if not in_video_devices:
            continue
        match = re.search(r"\[(\d+)]\s+(.+?)\s*$", line)
        if match:
            devices.append((int(match.group(1)), match.group(2)))
    return devices


def _normalize_avfoundation_name(value: str) -> str:
    normalized = value.strip().casefold()
    return normalized.translate(str.maketrans("", "", "\"'“”‘’"))


def _looks_like_camera_or_desktop_input(value: str) -> bool:
    normalized = _normalize_avfoundation_name(value)
    blocked_markers = (
        "camera",
        "desk view",
        "capture screen",
        "桌上视角",
        "相机",
        "摄像头",
    )
    return any(marker in normalized for marker in blocked_markers)


def _format_avfoundation_devices(devices: list[tuple[int, str]]) -> str:
    return ", ".join(f"[{index}] {name}" for index, name in devices) or "无"


def _is_tcp_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _subprocess_creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _read_mjpeg_stream_size(response, max_bytes: int = 2_000_000) -> Optional[tuple[int, int]]:
    data = bytearray()
    while len(data) < max_bytes:
        chunk = response.read(min(8192, max_bytes - len(data)))
        if not chunk:
            break
        data.extend(chunk)

        start = data.find(b"\xff\xd8")
        if start < 0:
            continue
        end = data.find(b"\xff\xd9", start + 2)
        if end < 0:
            continue

        return _jpeg_size(bytes(data[start : end + 2]))

    return None


def _jpeg_size(data: bytes) -> Optional[tuple[int, int]]:
    if not data.startswith(b"\xff\xd8"):
        return None

    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue

        marker = data[index + 1]
        index += 2
        while marker == 0xFF and index < len(data):
            marker = data[index]
            index += 1

        if marker in (0xD8, 0xD9):
            continue
        if index + 2 > len(data):
            return None

        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            return None

        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            if width > 0 and height > 0:
                return width, height
            return None

        index += segment_length

    return None
