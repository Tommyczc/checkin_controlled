from utils import *


def pack_app():
    run_cmd('PyInstaller app.spec --clean --noconfirm', "错误：pyinstaller app.spec 执行失败")

if __name__ == "__main__":
    print("========================================")
    print(" Python 打包脚本")
    print("========================================")
    print("[1/1] 开始打包")
    pack_app()
    print("========================================")
    print("打包成功！")
    print("========================================")