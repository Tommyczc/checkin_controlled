"""
BLE 监听脚本 - 使用 bleak 扫描并打印所有附近的 BLE 设备。
可针对钉钉蓝牙打卡器调整过滤条件。
"""

import asyncio
import sys
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# 可配置参数
RSSI_THRESHOLD = -70          # 只显示信号强度大于此值的设备（例如 -70dBm），设为 None 则不过滤
NAME_KEYWORD = "Tommy(2)"     # 设备名称包含此关键字才显示，设为 None 则显示所有
SHOW_RAW_DATA = True          # 是否显示原始广播数据（十六进制）

def format_adv_data(adv_data: AdvertisementData) -> str:
    """将广播数据格式化为可读字符串"""
    parts = []
    if adv_data.local_name:
        parts.append(f"Name: {adv_data.local_name}")
    parts.append(f"RSSI: {adv_data.rssi}")
    if adv_data.service_uuids:
        parts.append(f"Service UUIDs: {', '.join(adv_data.service_uuids)}")
    if adv_data.manufacturer_data:
        manu_str = []
        for company_id, data in adv_data.manufacturer_data.items():
            hex_data = data.hex() if SHOW_RAW_DATA else f"<{len(data)} bytes>"
            manu_str.append(f"0x{company_id:04X}: {hex_data}")
        parts.append(f"Manufacturer: {', '.join(manu_str)}")
    if adv_data.service_data:
        srv_str = []
        for uuid, data in adv_data.service_data.items():
            hex_data = data.hex() if SHOW_RAW_DATA else f"<{len(data)} bytes>"
            srv_str.append(f"{uuid}: {hex_data}")
        parts.append(f"Service Data: {', '.join(srv_str)}")
    return ", ".join(parts)

def detection_callback(device: BLEDevice, advertisement_data: AdvertisementData):
    """每次收到广播包时调用"""
    # 应用 RSSI 过滤
    if RSSI_THRESHOLD is not None and advertisement_data.rssi < RSSI_THRESHOLD:
        return
    # 应用名称关键字过滤
    if NAME_KEYWORD is not None:
        name = advertisement_data.local_name or device.name or ""
        if NAME_KEYWORD.lower() not in name.lower():
            return

    # 打印设备信息
    print(f"\n[Device] {device.address}")
    print(f"  {format_adv_data(advertisement_data)}")
    # 打印设备广播的原始数据（底层 bytes）
    if SHOW_RAW_DATA and advertisement_data.manufacturer_data:
        # 如果有制造商数据，上面已经打印了十六进制，这里不再重复
        pass
    sys.stdout.flush()

async def main():
    print("开始扫描 BLE 设备...")
    print \
        (f"过滤条件: RSSI > {RSSI_THRESHOLD if RSSI_THRESHOLD else '不限'}, 名称包含: {NAME_KEYWORD if NAME_KEYWORD else '不限'}")
    print("按 Ctrl+C 停止扫描\n")

    scanner = BleakScanner(detection_callback)
    try:
        await scanner.start()
        # 保持扫描直到用户中断
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\n用户中断扫描")
    finally:
        await scanner.stop()
        print("扫描已停止")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass