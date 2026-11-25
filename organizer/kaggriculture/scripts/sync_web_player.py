"""把单文件播放器同步成站点可用版本。

站点 CSP 是 script-src 'self'; style-src 'self'（内联脚本与 <style> 都被禁），
所以站点侧必须外链：
    frontend/public/replay.html  ← 去掉内联 <style>/<script>，改为外链
    frontend/public/replay.css   ← 原 <style> 内容
    frontend/public/replay.js    ← 原 <script> 内容

唯一实现仍然是 viz/player.html（可离线单文件打开）；本脚本只做确定性拆分，
两个文件头都会写入源文件 sha256，便于发现漂移。

用法：
    python scripts/sync_web_player.py                 # 同步到默认站点目录
    python scripts/sync_web_player.py --site <public目录> --check   # 只校验是否需要同步
"""
import argparse
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "viz", "player.html")
DEFAULT_SITE = r"C:\Users\TanPowasd\Desktop\算法雷达站\frontend\public"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_player(text):
    styles = re.findall(r"<style>\n(.*?)\n</style>", text, re.S)
    scripts = re.findall(r"<script>\n(.*?)\n</script>", text, re.S)
    if len(styles) != 1 or len(scripts) != 1:
        raise SystemExit("player.html 结构变了：style=%d script=%d（预期各 1）" % (len(styles), len(scripts)))
    return styles[0], scripts[0]


def build(text):
    css, js = split_player(text)
    h = sha(text)
    html = re.sub(r"<style>\n.*?\n</style>",
                  '<link rel="stylesheet" href="/replay.css">', text, count=1, flags=re.S)
    html = re.sub(r"<script>\n.*?\n</script>",
                  '<script src="/replay.js"></script>', html, count=1, flags=re.S)
    banner = "<!-- 由 %s 从 viz/player.html 生成；" \
             "源文件 sha256=%s；请改源文件后重新同步，勿直接改这里 -->\n" % (os.path.abspath(__file__), h)
    head_css = "/* 由 sync_web_player.py 从 viz/player.html 生成；源文件 sha256=%s；请勿直接改 */\n" % h
    head_js = head_css
    return banner + html, head_css + css + "\n", head_js + js + "\n", h


def main():
    ap = argparse.ArgumentParser(description="同步单文件播放器到站点（拆成 html/css/js）")
    ap.add_argument("--site", default=DEFAULT_SITE, help="站点 frontend/public 目录")
    ap.add_argument("--check", action="store_true", help="只检查是否需要同步")
    args = ap.parse_args()

    if not os.path.isfile(SRC):
        raise SystemExit("找不到源文件：%s" % SRC)
    text = open(SRC, encoding="utf-8").read()
    html, css, js, h = build(text)
    os.makedirs(args.site, exist_ok=True)
    changed = []
    for name, content in (("replay.html", html), ("replay.css", css), ("replay.js", js)):
        path = os.path.join(args.site, name)
        old = open(path, encoding="utf-8").read() if os.path.isfile(path) else None
        if old != content:
            changed.append(name)
            if not args.check:
                open(path, "w", encoding="utf-8").write(content)
    print("源文件 sha256=%s（%d 字节）" % (h[:16], len(text)))
    if not changed:
        print("站点已是最新，无需同步。")
        return 0
    print(("需要同步：" if args.check else "已同步：") + ", ".join(changed))
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
