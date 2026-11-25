"""裂陆纪元提交入口：评测机在沙箱里运行 `python3 main.py`。只能用 Python 标准库。

协议由 sdk.py 处理（分页、窗口号、命令 id）。你只需要改 bot.py 里的 Bot.decide。
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sdk import Client  # noqa: E402  （与选手包根目录 sdk.py 相同，必须一起打进 zip）
from bot import Bot  # noqa: E402


def main():
    client = Client()
    init = client.receive()          # 开局：公开地图、目录（科技/配方/军品）、本国初始观测、时钟、时间预算
    bot = Bot(init)
    client.ready()                   # 15 秒内必须回 ready
    while True:
        message = client.receive()   # 每个决策窗口一帧本国过滤观测
        if message.get('type') == 'end':
            return
        try:
            orders = bot.decide(message)
        except Exception:            # 策略 bug 不要让整个进程崩溃（崩溃会被托管为空命令）
            traceback.print_exc(file=sys.stderr)
            orders = []
        client.orders(message['window'], orders[:256])   # 每窗口最多 256 条


if __name__ == '__main__':
    main()
