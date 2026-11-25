# -*- coding: utf-8 -*-
"""生成聊天模式参赛完整题面 prompts/task.txt。"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel_path, root=ROOT):
    with open(os.path.join(root, rel_path), encoding="utf-8") as f:
        return f.read()


def build_prompt(root=ROOT):
    # 聊天模式无文件工具，所有必读材料在下方展开，覆盖文件夹模式的交付指令。
    parts = [
        "这是聊天模式参赛题面。所有必读资料已在下方展开，无须访问文件路径。\n"
        "请基于这些资料编写参赛策略（文件夹模式支持多文件 .zip 压缩包；聊天模式请返回完整的 main.py 代码块"
        "或清晰标注的多文件代码块）；不要声称已写入文件或已运行本地测试。\n\n" + read("AI_README.md", root)
    ]
    parts.append("=" * 30 + " 选手手册 " + "=" * 30)
    parts.append(read("handbook/player-guide.md", root))
    parts.append("=" * 30 + " 游戏事实卡 " + "=" * 30)
    parts.append(read("handbook/game-facts.md", root))
    parts.append("=" * 30 + " 多文件包模板 agents/template/main.py " + "=" * 30)
    parts.append(read("agents/template/main.py", root))
    parts.append("=" * 30 + " 多文件包模板 agents/template/planner.py " + "=" * 30)
    parts.append(read("agents/template/planner.py", root))
    parts.append("=" * 30 + " 多文件包模板 agents/template/params.json " + "=" * 30)
    parts.append(read("agents/template/params.json", root))
    parts.append("=" * 30 + " 官方规则源码（只用于理解，勿整份复制到提交） " + "=" * 30)
    parts.append(read("engine/rules.py", root))
    parts.append("=" * 30 + " 输出要求 " + "=" * 30)
    parts.append("现在输出完整可运行的策略代码（单文件 main.py 或按文件名标注的多文件包，全文不要省略、不要 TODO）。\n")
    return "\n\n".join(parts)


def main():
    out = os.path.join(ROOT, "prompts")
    os.makedirs(out, exist_ok=True)
    text = build_prompt()
    path = os.path.join(out, "task.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("题面已生成:", path, "(%d 字符)" % len(text))


if __name__ == "__main__":
    main()
