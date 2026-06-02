from pathlib import Path
from pyscfg import SimpleConfigs
from utils.log import MyLogger


DEFAULT_SETTINGS={
        "server.port": 8080,
        "server.ip": "127.0.0.1",

        "android.refresh_interval": 5,
        "android.auto_connect_remote": False,
        "android.mirror.max_fps": 60,
        "android.mirror.bit_rate": 8_000_000,
        "android.mirror.max_size": 1600,
        "android.mirror.video_codec": "h264",
        "android.mirror.show": False,
        "android.tasks.enabled": [],
    }

logger_instance = MyLogger()
logger = logger_instance.get_logger()


class ConfigController():

    def __init__(self):
        self.config_path = Path(__file__).parent.parent /"config.yaml"
        self.cfg = SimpleConfigs(defaults=DEFAULT_SETTINGS, file=str(self.config_path))
        logger.info("配置控制器初始化完成，配置文件路径: %s", self.config_path)

    def property_exists(self, property:str):
        return property in self.cfg.configs

    def get(self, property:str):
        if self.property_exists(property):
            return self.cfg[property]
        else:
            logger.warning("读取配置项失败，配置不存在: %s", property)
            return None

    def set(self, property:str, value):
        self.cfg[property] = value
        logger.info("配置项已更新: %s=%s", property, value)

    def delete(self, property:str):
        if self.property_exists(property):
            self.cfg.remove(property)
            logger.info("配置项已删除: %s", property)
        else:
            logger.warning("删除配置项失败，配置不存在: %s", property)

config=ConfigController()
