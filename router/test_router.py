from fastapi import APIRouter

router = APIRouter(
    prefix="/test",
    tags=["test"],
)

@router.get("/test_connection")
async def test_connection():
    """获取所有设备"""
    return {"1":"aalalalal"}