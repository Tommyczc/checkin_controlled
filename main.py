from contextlib import asynccontextmanager

import logging
import uvicorn
from fastapi import FastAPI

from controller import config_controller, devices_controller
from controller.config_controller import ConfigController
from controller.devices_controller import DevicesController
from router import test_router, mirror_router, operation_router, device_router
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()


def configure_server_logging() -> None:
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        target_logger = logging.getLogger(logger_name)
        target_logger.handlers.clear()
        target_logger.addHandler(logger_instance.stream_handler)
        target_logger.addHandler(logger_instance.file_handler)
        target_logger.setLevel(logging.INFO)
        target_logger.propagate = False

def get_config()->ConfigController:
    return config_controller.config

def get_device()->DevicesController:
    return devices_controller.devices

# 定义 lifespan 异步上下文管理器
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ---------- 启动逻辑 (startup) ----------
    logger.info("Server Starting Up...")
    logger.info(f"Server IP: {get_config().get('server.ip')}, Server port: {get_config().get('server.port')}")

    yield

    # ---------- 关闭逻辑 (shutdown) ----------
    logger.info("Server Shutting Down...")

app = FastAPI(lifespan=lifespan)
app.include_router(test_router.router)
app.include_router(mirror_router.router)
app.include_router(operation_router.router)
app.include_router(device_router.router)


if __name__ == "__main__":
    configure_server_logging()
    uvicorn.run(
        app,
        host=get_config().get("server.ip"),
        port=get_config().get("server.port"),
        log_config=None,
        access_log=True,
    )
