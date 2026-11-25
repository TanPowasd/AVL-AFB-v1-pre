# -*- coding: utf-8 -*-
"""生成聊天模式单文件完整参赛题面 prompts/task.txt（零外部依赖、零私有词）。"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel_path: str, root: str = ROOT) -> str:
    with open(os.path.join(root, rel_path), encoding="utf-8") as fh:
        return fh.read().strip()


def build_prompt(root: str = ROOT) -> str:
    parts = [
        "这是聊天模式参赛题面（Cache Duel 缓存攻防）。所有必读资料已在下方展开，无须访问文件路径。\n"
        "请基于这些资料编写参赛策略，只返回一个包含完整单文件 Python 3.10+ 代码（同时支持 attacker 与 defender 角色）的代码块；"
        "不要声称已写入文件或已运行本地测试。\n\n" + read("AI_README.md", root),
        "=" * 30 + " 选手手册 " + "=" * 30,
        read("handbook/player-guide.md", root),
        "=" * 30 + " 游戏事实卡 " + "=" * 30,
        read("handbook/game-facts.md", root),
        "=" * 30 + " 起步模板 agents/starter.py " + "=" * 30,
        read("agents/starter.py", root),
        "=" * 30 + " 官方裁判源码 engine/engine.py（只用于理解，勿整份复制到提交） " + "=" * 30,
        read("engine/engine.py", root),
        "=" * 30 + " 输出要求 " + "=" * 30,
        "现在只输出一个 Python 代码块，内容为完整单文件提交代码（全文，不要省略、不要解释、不要 TODO）。\n",
    ]
    return "\n\n".join(parts)


def main() -> None:
    out_dir = os.path.join(ROOT, "prompts")
    os.makedirs(out_dir, exist_ok=True)
    text = build_prompt()
    out_path = os.path.join(out_dir, "task.txt")
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"题面已生成: {out_path} ({len(text)} 字符)")


if __name__ == "__main__":
    main()
