from fastapi import APIRouter
from utils.log import MyLogger

logger_instance = MyLogger()
logger = logger_instance.get_logger()

router = APIRouter(
    prefix="/test",
    tags=["test"],
)

@router.get("/test_connection")
async def test_connection():
    """获取所有设备"""
    logger.info("收到测试连接请求")
    return {"1":"aalalalal"}
