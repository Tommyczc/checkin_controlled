
import logging
import os
import sys
from datetime import datetime

from colorlog import ColoredFormatter

class MyLogger:
    _instance = None

    def __new__(cls, log_file="app.log"):
        if not cls._instance:
            cls._instance = super(MyLogger, cls).__new__(cls)
            cls._instance.setup_logger()
        return cls._instance

    def setup_logger(self, main_log_level=logging.INFO, console_log_level=logging.INFO, file_log_level=logging.INFO):
        # 获取当前日期作为文件名的一部分
        current_date = datetime.now().strftime('%Y%m%d')

        # 创建 result 文件夹
        dir_ = os.path.realpath(os.path.dirname(sys.argv[0]))+ '/log'
        os.makedirs(dir_, exist_ok=True)

        # 修改日志文件路径为 result 文件夹下的文件
        log_file_path = os.path.join(dir_, f'{current_date}.log')

        formatter = ColoredFormatter(
            "%(log_color)s%(asctime)s - %(levelname)s - %(message)s%(reset)s",
            datefmt=None,
            reset=True,
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            },
            secondary_log_colors={},
            style='%'
        )

        self.stream_handler = logging.StreamHandler()
        self.stream_handler.setFormatter(formatter)
        self.stream_handler.setLevel(console_log_level)

        self.file_handler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8', delay=False)
        self.file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        self.file_handler.setLevel(file_log_level)  # log文件保存到INFO级别就可以了

        self.logger = logging.getLogger(__name__)
        self.logger.addHandler(self.stream_handler)
        self.logger.addHandler(self.file_handler)
        self.logger.setLevel(main_log_level)

    def get_logger(self):
        return self.logger

    # 感觉下面的都用不到,不设置也没关系
    def set_file_log_level(self, level):
        self.file_handler.setLevel(level)

    def set_console_log_level(self, level):
        self.stream_handler.setLevel(level)

    def set_main_log_level(self, level):
        self.logger.setLevel(level)




