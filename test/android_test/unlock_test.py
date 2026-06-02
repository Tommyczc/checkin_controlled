import uiautomator2 as u2
import time

def unlock_device(d, password="your_pin"):
    """
    解锁设备
    :param d: uiautomator2 的设备连接对象
    :param password: PIN码或密码
    """
    # 1. 点亮并唤醒设备
    d.screen_on()
    d.unlock() # 这一步会显示密码输入界面
    time.sleep(1) # 等待界面加载完成

    # 2. 输入密码
    try:
        password_input = d(resourceId="com.android.systemui:id/input_field")
        if password_input.exists:
            password_input.click()
            password_input.clear_text()
            password_input.send_keys(password)
            print("密码已输入")
            # 3. 确认解锁 (有些手机需要再点击"OK"或"下一步")
            ok_button = d(text="OK")
            if ok_button.exists:
                ok_button.click()
            # 或者按回车键确认
            # d.press("enter")
            time.sleep(1)
            print("设备已解锁")
        else:
            print("未找到密码输入框，可能设备已解锁或界面元素不正确")
    except Exception as e:
        print(f"解锁过程中出错: {e}")

if __name__ == '__main__':
    d = u2.connect(serial="MDX0220723002243")
    unlock_device(d, "258000")