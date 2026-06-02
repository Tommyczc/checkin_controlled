import subprocess

def get_devices():
    """
    执行 adb devices 命令，获取所有已连接设备的序列号列表。
    """
    devices = []
    # 执行 adb devices 命令
    cmd = subprocess.Popen('adb devices', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = cmd.communicate()

    if err:
        print(f'执行 adb devices 时出错: {err.decode("utf-8")}')
        return devices

    # 解析输出，通常格式为：
    # List of devices attached
    # emulator-5554   device
    # 192.168.1.100:5555   device
    # print(out.decode("utf-8"))
    for line in out.decode().splitlines():
        # 跳过开头的无用信息行，例如：List of devices attached
        if line.strip() and not line.startswith('List of devices attached'):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == 'device':
                devices.append(parts[0])
    return devices


if __name__ == '__main__':
    device_list = get_devices()
    print("已连接的设备:", device_list)


    # 遍历设备列表，为每个设备创建一个 uiautomator2 连接对象
    # for serial in device_list:
    #     print(f"正在为设备 {serial} 创建连接...")
    #     d = u2.connect(serial)  # 使用序列号连接特定设备[reference:1]
        # 之后就可以使用 d 变量来操作这个设备了
        # 例如: print(d.info)