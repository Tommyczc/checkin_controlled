from pathlib import Path
from pyscfg import SimpleConfigs


DEFAULT_SETTINGS={
        "server.port": 8080,
        "server.ip": "",

        "android.refresh_interval": 5,
        "android.auto_connect_remote": False,
        "android.mirror.max_fps": 60,
        "android.mirror.bit_rate": 8_000_000,
        "android.mirror.max_size": 1600,
        "android.mirror.video_codec": "h264",
        "android.mirror.show": False,
        "android.tasks.enabled": [],
    }


class ConfigController():

    def __init__(self):
        self.config_path = Path(__file__).parent.parent / "config" / "config.yaml"
        self.cfg = SimpleConfigs(defaults=DEFAULT_SETTINGS, file=str(self.config_path))

    def property_exists(self, property:str):
        return property in self.cfg.configs

    def get(self, property:str):
        if self.property_exists(property):
            return self.cfg[property]
        else:
            return None

    def set(self, property:str, value):
        self.cfg[property] = value

    def delete(self, property:str):
        if self.property_exists(property):
            self.cfg.remove(property)

config=ConfigController()