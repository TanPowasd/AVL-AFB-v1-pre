# -*- coding: utf-8 -*-
"""生成聊天模式自包含题面 prompts/task.txt（汇总 AI_README、handbook 与 starter.py）。"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel_path: str, root: str = ROOT) -> str:
    with open(os.path.join(root, rel_path), encoding="utf-8") as fh:
        return fh.read().strip()


def build_prompt(root: str = ROOT) -> str:
    preamble = (
        "这是前沿实验室（Frontier Lab / ai_training）聊天模式自包含参赛题面。"
        "所有必读资料已在下方展开，无须访问外部文件路径。\n"
        "注：文件夹模式推荐打包多文件 .zip（根目录含 main.py）；当前为聊天模式，"
        "请基于这些资料直接编写参赛策略，只返回一个包含完整单文件 Python 3.10+ 策略代码的代码块（可作为 main.py 运行）；"
        "仅使用 Python 标准库，不要省略逻辑、不要留 TODO，也不要声称已写入文件或运行本地测试。"
    )
    sections = [
        preamble + "\n\n" + read("AI_README.md", root),
        "=" * 28 + " 公开赛制与计分 " + "=" * 28,
        read("handbook/contest-rules.md", root),
        "=" * 28 + " 选手手册（协议与动作） " + "=" * 28,
        read("handbook/player-guide.md", root),
        "=" * 28 + " 游戏事实卡（精确数值与公式） " + "=" * 28,
        read("handbook/game-facts.md", root),
        "=" * 28 + " 起步模板 agents/starter.py " + "=" * 28,
        "```python\n" + read("agents/starter.py", root) + "\n```",
        "=" * 28 + " 输出要求 " + "=" * 28,
        "现在只输出一个完整的 Python 代码块，实现可直接作为子进程运行的前沿实验室参赛策略（全文无省略）。\n",
    ]
    return "\n\n".join(sections)


def main() -> None:
    out_dir = os.path.join(ROOT, "prompts")
    os.makedirs(out_dir, exist_ok=True)
    text = build_prompt(ROOT)
    out_path = os.path.join(out_dir, "task.txt")
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"题面已生成：{out_path} ({len(text)} 字符)")


if __name__ == "__main__":
    main()
