import subprocess
import threading
import time
from typing import Optional

class ScrcpyStreamer:
    """管理 scrcpy 子进程，获取屏幕视频流（原始 H.264）"""

    def __init__(self, device_id: str, max_fps: int = 30, bit_rate: int = 2_000_000):
        """
        :param device_id: adb 设备序列号
        :param max_fps: 最大帧率
        :param bit_rate: 比特率 (bps)
        """
        self.device_id = device_id
        self.max_fps = max_fps
        self.bit_rate = bit_rate
        self.process: Optional[subprocess.Popen] = None
        self._stop_flag = False

    def start(self) -> None:
        """启动 scrcpy 子进程，视频流输出到 stdout"""
        if self.process is not None:
            raise RuntimeError("Scrcpy process already running")
        cmd = [
            "scrcpy",
            "--no-window",              # 不创建显示窗口
            "--no-playback",            # 不播放音频（仅视频）
            "--serial", self.device_id,
            "--max-fps", str(self.max_fps),
            "--bit-rate", str(self.bit_rate),
            "--record", "-",             # 输出到标准输出
        ]
        # 启动进程，stdout 为管道，stderr 继承（方便调试）
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=None,   # 或者 subprocess.PIPE 自行处理
            bufsize=0,     # 无缓冲
        )
        self._stop_flag = False

    def stop(self) -> None:
        """关闭 scrcpy 进程"""
        if self.process is None:
            return
        self._stop_flag = True
        # 尝试优雅终止
        self.process.terminate()
        # 等待几秒后强制杀死
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process = None

    @property
    def stdout(self):
        """返回子进程的 stdout 文件对象，用于读取原始 H.264 流"""
        if self.process is None:
            raise RuntimeError("Process not started")
        return self.process.stdout

    def is_running(self) -> bool:
        """检查进程是否仍在运行"""
        return self.process is not None and self.process.poll() is None


# ========== 使用示例：用 OpenCV 解码并显示 ==========
import cv2
import numpy as np
import io

def decode_h264_stream(stream, display_name="Scrcpy"):
    """
    从可读的 stream 中持续读取 H.264 数据，解码并显示。
    这里使用 av 库或直接调用 ffmpeg 管道会更高效，
    但为方便演示，使用一个简单的缓存 + cv2 解码器。
    """
    # 更稳健的方式：启动 ffmpeg 子进程解码
    # 参考下面的实现
    pass

def main_av():
    """使用 av 库解码（推荐）"""
    try:
        import av
    except ImportError:
        print("请安装 av 库：pip install av")
        return

    streamer = ScrcpyStreamer(device_id="10CF1E2C5S0023A")
    streamer.start()
    print("scrcpy 已启动，按 Ctrl+C 退出")

    # 使用 PyAV 从管道中读取
    # av.open 可以直接接受文件对象
    container = av.open(streamer.stdout, 'r')
    try:
        for packet in container.demux():
            if packet.stream.type == 'video':
                for frame in packet.decode():
                    img = frame.to_ndarray(format='bgr24')
                    cv2.imshow("Scrcpy Stream", img)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        streamer.stop()
        cv2.destroyAllWindows()

def main_cv_pipe():
    """备用：使用 OpenCV 的 VideoCapture 从管道读取（需要命名管道，较复杂，不推荐）"""
    # 略
    pass

if __name__ == "__main__":
    main_av()