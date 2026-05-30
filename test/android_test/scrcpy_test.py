import subprocess
import cv2
import numpy as np
import queue
import threading


class ScrcpyStream:
    def __init__(self):
        self.process = None
        self.frame_queue = queue.Queue(maxsize=2)  # 限制队列大小，避免内存积压
        self.running = False
        self.decoder_thread = None

    def start(self, device_id=None):
        """启动 scrcpy 进程并开始解码"""
        cmd = [
            "scrcpy", "--no-window", "--no-playback", "--record", "-",
            "--max-fps", "30", "--bit-rate", "4M"
        ]
        if device_id:
            cmd.extend(["--serial", device_id])

        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.running = True
        self.decoder_thread = threading.Thread(target=self._decode_loop)
        self.decoder_thread.start()

    def _decode_loop(self):
        """在后台线程中解码 H.264 流"""
        # 使用 ffmpeg 子进程解码
        ffmpeg_cmd = [
            "ffmpeg", "-i", "pipe:0",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-vsync", "0", "pipe:1"
        ]
        ffmpeg = subprocess.Popen(
            ffmpeg_cmd,
            stdin=self.process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

        # 读取原始帧数据
        width, height = 1080, 1920  # 可从设备获取实际分辨率
        frame_size = width * height * 3

        while self.running and ffmpeg.poll() is None:
            raw_frame = ffmpeg.stdout.read(frame_size)
            if len(raw_frame) != frame_size:
                continue
            frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape(height, width, 3)
            # 使用 queue.put_nowait 避免阻塞，队列满时丢弃旧帧
            try:
                self.frame_queue.put_nowait(frame)
            except queue.Full:
                pass

    def read(self):
        """获取最新一帧（非阻塞）"""
        try:
            return self.frame_queue.get_nowait()
        except queue.Empty:
            return None

    def stop(self):
        self.running = False
        if self.process:
            self.process.terminate()
        if self.decoder_thread:
            self.decoder_thread.join()


# 使用示例
stream = ScrcpyStream()
stream.start()

while True:
    frame = stream.read()
    if frame is not None:
        cv2.imshow("Screen", frame)
    if cv2.waitKey(1) == ord('q'):
        break

stream.stop()
cv2.destroyAllWindows()