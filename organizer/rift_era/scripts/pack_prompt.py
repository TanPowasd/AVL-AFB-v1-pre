"""生成裂陆纪元（rift_era）聊天模式独立参赛题面 kit/prompts/task.txt。

将公开参赛指引、手册、事实卡与起步模板合并为紧凑、纯净的单一文本（不内联全部引擎文件），
并在写入前执行纯净检查，确保不含任何私有标记或禁用词。

用法：
    python3 organizer/rift_era/scripts/pack_prompt.py
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]   # organizer/rift_era
KIT = ROOT / 'kit'
OUT = KIT / 'prompts' / 'task.txt'

FORBIDDEN_WORDS = re.compile(r'organizer|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd|/data/ws|/data/home')
PRIVATE_MARKERS = ('scoring_v2', 'shadow_cache', 'RIFT_ERA_BOTS_ROOT', 'bots/super',
                   'bots/hegemon', 'bots/arbiter', 'bots/verdant', 'hegemon', 'arbiter', 'verdant')


def read_kit(rel: str) -> str:
    return (KIT / rel).read_text(encoding='utf-8').strip()


def build_prompt() -> str:
    parts = [
        '这是裂陆纪元（rift_era）的聊天模式参赛题面。所有核心规则、协议、事实卡与起步模板均已在下方展开，无需读取本地文件路径。\n'
        '请直接编写完整的参赛策略代码（Python 3.11+ 标准库唯一），输出完整可运行的 `bot.py`（默认搭配下方 `main.py` 与标准库 `sdk.py`）；'
        '不要省略实现、不要留 TODO、不要声称已写入文件或运行本地测试。\n\n'
        + read_kit('AI_README.md'),
        '=' * 28 + ' 公开赛制（FORMAT.md） ' + '=' * 28 + '\n\n' + read_kit('handbook/contest-rules.md'),
        '=' * 28 + ' 选手手册（player-guide.md） ' + '=' * 28 + '\n\n' + read_kit('handbook/player-guide.md'),
        '=' * 28 + ' 游戏事实卡（game-facts.md） ' + '=' * 28 + '\n\n' + read_kit('handbook/game-facts.md'),
        '=' * 28 + ' 命令表（COMMANDS.md） ' + '=' * 28 + '\n\n' + read_kit('COMMANDS.md'),
        '=' * 28 + ' 起步入口模板 main.py ' + '=' * 28 + '\n\n```python\n' + read_kit('template/main.py') + '\n```',
        '=' * 28 + ' 起步策略模板 bot.py ' + '=' * 28 + '\n\n```python\n' + read_kit('template/bot.py') + '\n```',
        '=' * 28 + ' 输出要求 ' + '=' * 28 + '\n\n'
        '现在请输出一个完整的 `bot.py` Python 代码块（保留 `class Bot:` 及 `decide(self, obs)` 接口，'
        '仅使用 Python 标准库，确保任何异常均被妥善处理，不要省略代码或留 TODO）。\n',
    ]
    text = '\n\n'.join(parts)
    m = FORBIDDEN_WORDS.search(text)
    if m:
        raise ValueError(f'生成的题面包含禁用词：{m.group(0)!r}')
    for marker in PRIVATE_MARKERS:
        if marker in text:
            raise ValueError(f'生成的题面包含私有标记：{marker!r}')
    return text


def main() -> int:
    text = build_prompt()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding='utf-8', newline='\n')
    print(f'题面已生成：{OUT}（{len(text)} 字符）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
