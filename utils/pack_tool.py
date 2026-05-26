import subprocess
import sys


def run_cmd(cmd, error_msg):
    try:
        # 推荐使用 sys.executable 获取当前 Python 解释器路径，确保环境一致性
        subprocess.run([sys.executable, '-m', *cmd.split()], check=True)
    except subprocess.CalledProcessError:
        print(error_msg)
        sys.exit(1)