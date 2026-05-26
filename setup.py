import sys
import os
from utils.pack_tool import run_cmd


def check_and_install_pip_tools():
    try:
        __import__('piptools')
        print("pip-tools 已安装。")
    except ImportError:
        print("pip-tools 未安装，正在安装...")
        run_cmd('pip install pip-tools', "错误：pip-tools 安装失败。")

def check_requirements_in():
    if not os.path.exists("requirements.in"):
        print("错误：未找到 requirements.in 文件，请先创建依赖描述文件。")
        sys.exit(1)
    print("requirements.in 文件存在。")

def generate_and_install():
    run_cmd('piptools compile --quiet --strip-extras requirements.in', "错误：pip-compile 执行失败。")
    print("requirements.txt 生成成功。")
    run_cmd('piptools sync requirements.txt', "依赖同步失败")

def main():
    print("========================================")
    print(" Python 依赖环境设置脚本")
    print("========================================")
    print("[1/3] 检查 pip-tools 是否已安装...")
    check_and_install_pip_tools()
    print("[2/3] 检查依赖描述文件 requirements.in ...")
    check_requirements_in()
    print("[3/3] 正在生成 requirements.txt 并安装依赖...")
    generate_and_install()
    print("========================================")
    print("所有依赖已就绪，环境准备完成！")
    print("========================================")

if __name__ == "__main__":
    main()