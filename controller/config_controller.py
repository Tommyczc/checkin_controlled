import os
import platform
from pathlib import Path

from pyscfg import SimpleConfigs
from utils.log import MyLogger


# 这里放的是“示例路径”，方便使用者直接照着改。
# 如果不想由项目覆盖系统 PATH，可保留为空字符串。
EXAMPLE_TOOL_ROOTS = {
        "tools.windows.adb_root_dir": "",
        "tools.windows.scrcpy_root_dir": r"D:\tools\scrcpy-win64-v4.0",
        "tools.macos.adb_root_dir": "/opt/homebrew/bin",
        "tools.macos.scrcpy_root_dir": "/opt/homebrew/bin",
    }


DEFAULT_SETTINGS={
        "server.port": 8080,
        "server.ip": "127.0.0.1",

        "android.refresh_interval": 5,
        "android.auto_connect_remote": False,
        "android.mirror.max_fps": 30,
        "android.mirror.bit_rate": 4_000_000,
        "android.mirror.max_size": 1280,
        "android.mirror.output_max_fps": 15,
        "android.mirror.output_max_size": 720,
        "android.mirror.jpeg_quality": 55,
        "android.mirror.video_codec": "h264",
        "android.mirror.show": False,
        "android.tasks.enabled": [],

        # 工具根目录示例：
        # - Windows adb_root_dir: adb.exe 所在目录；留空时优先使用 scrcpy_root_dir 下的 adb.exe
        # - Windows scrcpy_root_dir: scrcpy.exe 与 scrcpy-server 所在目录
        # - macOS adb_root_dir/scrcpy_root_dir: 常见为 /opt/homebrew/bin
        # 使用者可直接改成自己的真实安装目录。
        "tools.windows.adb_root_dir": EXAMPLE_TOOL_ROOTS["tools.windows.adb_root_dir"],
        "tools.windows.scrcpy_root_dir": EXAMPLE_TOOL_ROOTS["tools.windows.scrcpy_root_dir"],
        "tools.macos.adb_root_dir": EXAMPLE_TOOL_ROOTS["tools.macos.adb_root_dir"],
        "tools.macos.scrcpy_root_dir": EXAMPLE_TOOL_ROOTS["tools.macos.scrcpy_root_dir"],
    }

logger_instance = MyLogger()
logger = logger_instance.get_logger()


class ConfigController():

    def __init__(self):
        self.config_path = Path(__file__).parent.parent /"config.yaml"
        self.cfg = SimpleConfigs(defaults=DEFAULT_SETTINGS, file=str(self.config_path))
        self._configure_platform_tool_paths()
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

    def _configure_platform_tool_paths(self) -> None:
        platform_key = self._get_platform_key()
        if platform_key is None:
            logger.info("当前平台无需覆盖 adb/scrcpy 路径: %s", platform.system())
            return

        scrcpy_root = self._get_tool_root_dir(platform_key, "scrcpy")
        adb_root = self._get_adb_root_dir(platform_key, scrcpy_root)
        candidate_dirs = [path for path in [adb_root, scrcpy_root] if path is not None and path.exists()]
        if candidate_dirs:
            self._prepend_to_path(candidate_dirs)

        adb_executable = self._resolve_executable_from_root(adb_root, ["adb.exe", "adb"])
        if adb_executable is not None:
            os.environ["ADB"] = str(adb_executable)
            os.environ["ADBUTILS_ADB_PATH"] = str(adb_executable)
            logger.info("当前平台 adb 路径已生效: %s", adb_executable)

        scrcpy_executable = self._resolve_executable_from_root(scrcpy_root, ["scrcpy.exe", "scrcpy"])
        if scrcpy_executable is not None:
            os.environ["SCRCPY"] = str(scrcpy_executable)
            logger.info("当前平台 scrcpy 路径已生效: %s", scrcpy_executable)

        scrcpy_server_path = self._resolve_executable_from_root(
            scrcpy_root,
            ["scrcpy-server", "scrcpy-server.jar"],
        )
        if scrcpy_server_path is not None:
            os.environ["SCRCPY_SERVER_PATH"] = str(scrcpy_server_path)
            logger.info("当前平台 scrcpy-server 路径已生效: %s", scrcpy_server_path)

    def _get_platform_key(self) -> str | None:
        system_name = platform.system()
        if system_name == "Windows":
            return "windows"
        if system_name == "Darwin":
            return "macos"
        return None

    def _get_tool_root_dir(self, platform_key: str, tool_name: str) -> Path | None:
        property_name = f"tools.{platform_key}.{tool_name}_root_dir"
        root_dir = self.get(property_name)
        if not root_dir:
            return None

        path = Path(str(root_dir)).expanduser()
        if not path.exists():
            if str(root_dir) == EXAMPLE_TOOL_ROOTS.get(property_name):
                logger.info("工具根目录仍为示例值，未启用平台工具覆盖: %s=%s", property_name, path)
            else:
                logger.warning("配置的工具根目录不存在: %s=%s", property_name, path)
            return None
        return path

    def _get_adb_root_dir(self, platform_key: str, scrcpy_root: Path | None) -> Path | None:
        configured_adb_root = self._get_tool_root_dir(platform_key, "adb")
        if platform_key != "windows":
            return configured_adb_root

        scrcpy_adb = self._resolve_executable_from_root(scrcpy_root, ["adb.exe", "adb"])
        if scrcpy_adb is not None:
            if configured_adb_root is not None and configured_adb_root != scrcpy_root:
                logger.info(
                    "Windows 下优先使用 scrcpy 目录内置 adb，忽略单独 adb_root_dir: %s",
                    configured_adb_root,
                )
            return scrcpy_root

        return configured_adb_root

    def _resolve_executable_from_root(self, root_dir: Path | None, candidates: list[str]) -> Path | None:
        if root_dir is None:
            return None

        for name in candidates:
            candidate = root_dir / name
            if candidate.exists():
                return candidate
        return None

    def _prepend_to_path(self, paths: list[Path]) -> None:
        current_path = os.environ.get("PATH", "")
        path_parts = [part for part in current_path.split(os.pathsep) if part]
        new_parts: list[str] = []

        for path in paths:
            path_str = str(path)
            if path_str not in new_parts and path_str not in path_parts:
                new_parts.append(path_str)

        if not new_parts:
            return

        os.environ["PATH"] = os.pathsep.join([*new_parts, *path_parts])
        logger.info("已按平台注入工具目录到 PATH: %s", new_parts)

config=ConfigController()
