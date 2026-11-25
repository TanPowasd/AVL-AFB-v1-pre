"""Hegemon scoring opponent entry point (python3 main.py). Standard library only."""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sdk import Client  # noqa: E402
from bot import Bot  # noqa: E402


def main():
    client = Client()
    init = client.receive()
    bot = Bot(init)
    client.ready()
    while True:
        message = client.receive()
        if message.get('type') == 'end':
            return
        try:
            orders = bot.decide(message)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            orders = []
        client.orders(message['window'], orders[:256])


if __name__ == '__main__':
    main()
