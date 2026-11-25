# -*- coding: utf-8 -*-
"""受约束的 Kaggriculture agent 加载器：支持 .zip 压缩包（多文件 Python 包 + 包内只读数据）、
含 main.py 的目录以及兼容单文件 .py，在独立子进程中执行并在正式评测时叠加 OS 沙箱隔离。

提交规范（推荐）：
- 提交单个 .zip 压缩包（根目录含 main.py，可附带任意子模块 .py 与只读数据文件如 .json/.bin/.csv）；
- main.py 提供模块级 def act(observation) -> dict 或 class Agent: def act(self, observation) -> dict；
- 允许使用完整 Python 3.10+ 标准库、导入压缩包内子模块、只读读取压缩包内数据文件；
- 独立子进程负责加载限时、单步超时强杀与进程崩溃隔离；正式评测时叠加系统级沙箱禁止联网、越界读写与超额内存。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import random
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import zipfile

MAX_COMPRESSED_BYTES = 64 * 1024 * 1024   # 64 MiB
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024   # 256 MiB
MAX_ARCHIVE_ENTRIES = 2048
MAX_COMPRESSION_RATIO = 100
LOAD_TIMEOUT = 60.0   # 加载提交（含模块顶层代码、读包内数据与 Agent() 初始化）的时限
TIMEOUT_GRACE = 0.05


class SandboxError(Exception):
    pass


def is_submission_path(path: str) -> bool:
    """判断路径是否为合法提交形态（.zip 压缩包、含 main.py 的目录、或 .py 单文件）。"""
    p = os.path.abspath(path)
    if os.path.isfile(p):
        low = p.lower()
        return low.endswith(".zip") or low.endswith(".py")
    if os.path.isdir(p):
        return os.path.isfile(os.path.join(p, "main.py"))
    return False


def submission_label(path: str) -> str:
    """提取提交标识名。"""
    p = os.path.normpath(os.path.abspath(path))
    base = os.path.basename(p)
    if os.path.isdir(p):
        return base
    return os.path.splitext(base)[0]


def sha256_path(path: str) -> str:
    """计算单个文件（.zip/.py）或目录（按排序路径哈希）的 SHA-256。"""
    p = Path(path).resolve()
    if p.is_file():
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    if p.is_dir():
        h = hashlib.sha256()
        for f in sorted(
            (x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts),
            key=lambda x: x.relative_to(p).as_posix(),
        ):
            rel = f.relative_to(p).as_posix().encode("utf-8")
            h.update(rel + b"\0" + hashlib.sha256(f.read_bytes()).hexdigest().encode("ascii") + b"\n")
        return h.hexdigest()
    raise FileNotFoundError(f"找不到提交路径：{path}")


def _validate_zip_info(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if "\x00" in info.orig_filename or "\x00" in name:
        raise SandboxError("压缩包条目含非法空字符 NUL")
    if not (info.flag_bits & 0x800) and not name.isascii():
        raise SandboxError(f"压缩包条目含非 UTF-8 路径：{name!r}")
    if info.flag_bits & 1:
        raise SandboxError(f"压缩包不支持加密条目：{name}")
    parts = name.rstrip("/").split("/")
    if (
        not name
        or "\\" in name
        or ":" in name
        or name.startswith("/")
        or any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in parts)
    ):
        raise SandboxError(f"压缩包条目路径不安全：{name!r}")
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise SandboxError(f"压缩包禁止非常规文件/符号链接：{name}")
    if not info.is_dir() and info.file_size > MAX_COMPRESSION_RATIO * max(1, info.compress_size):
        raise SandboxError(f"压缩包条目压缩比超限（>{MAX_COMPRESSION_RATIO}x）：{name}")
    return PurePosixPath(*parts)


def check_archive(path: str) -> list[str]:
    """校验 .zip 压缩包安全性与完整性，返回问题列表（空列表表示通过）。"""
    problems: list[str] = []
    p = Path(path)
    if not p.is_file():
        return [f"找不到压缩包：{path}"]
    size = p.stat().st_size
    if size > MAX_COMPRESSED_BYTES:
        return [f"压缩包体积 {size} 字节超过上限 {MAX_COMPRESSED_BYTES} 字节（64 MiB）"]
    try:
        with zipfile.ZipFile(p, "r") as zf:
            infos = zf.infolist()
            if not infos:
                return ["压缩包为空"]
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                return [f"压缩包条目数 {len(infos)} 超过上限 {MAX_ARCHIVE_ENTRIES}"]
            total_size = 0
            names_cf: set[str] = set()
            regular_cf: set[str] = set()
            parsed: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            for info in infos:
                try:
                    rel = _validate_zip_info(info)
                except SandboxError as exc:
                    problems.append(str(exc))
                    continue
                key = str(rel).casefold()
                if key in names_cf:
                    problems.append(f"压缩包存在大小写冲突路径：{rel}")
                    continue
                names_cf.add(key)
                if not info.is_dir():
                    regular_cf.add(key)
                    total_size += info.file_size
                parsed.append((info, rel))
            if total_size > MAX_EXTRACTED_BYTES:
                problems.append(
                    f"压缩包解压总大小 {total_size} 字节超过上限 {MAX_EXTRACTED_BYTES} 字节（256 MiB）"
                )
            for _, rel in parsed:
                if any(str(parent).casefold() in regular_cf for parent in rel.parents if str(parent) != "."):
                    problems.append(f"压缩包文件遮蔽目录：{rel}")
            has_main = any((not info.is_dir()) and str(rel) == "main.py" for info, rel in parsed)
            if not has_main:
                problems.append("压缩包根目录缺少入口文件 main.py")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError, OSError) as exc:
        problems.append(f"无法解析压缩包：{exc}")
    return problems


def extract_archive(archive_path: str, dest_dir: str) -> str:
    """校验并将 .zip 安全流式解压至 dest_dir（必须为空或新建目录）。"""
    problems = check_archive(archive_path)
    if problems:
        raise SandboxError("; ".join(problems))
    root = Path(dest_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                rel = _validate_zip_info(info)
                target = root.joinpath(*rel.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with zf.open(info, "r") as src, target.open("xb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        written += len(chunk)
                        if total > MAX_EXTRACTED_BYTES or written > info.file_size:
                            raise SandboxError("压缩包流式解压超出体积配额")
                        dst.write(chunk)
                if written != info.file_size:
                    raise SandboxError(f"压缩包条目解压大小不匹配：{rel}")
    except SandboxError:
        raise
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError, OSError) as exc:
        raise SandboxError(f"解压失败：{exc}") from exc
    if not (root / "main.py").is_file():
        raise SandboxError("压缩包根目录缺少入口文件 main.py")
    return str(root)


def check_source(src: str) -> list[str]:
    """兼容旧接口：现行规则允许完整标准库与多文件包，仅校验文本非空。"""
    if not isinstance(src, str) or not src.strip():
        return ["提交源码为空"]
    return []


def _extract_entry_from_namespace(ns: dict, label_hint: str):
    if "act" in ns and callable(ns["act"]):
        return ns["act"]
    if "Agent" in ns:
        inst = ns["Agent"]()
        if hasattr(inst, "act") and callable(inst.act):
            return inst.act
    raise SandboxError(f"{label_hint} 未提供 act(observation) 函数或 Agent.act 方法")


def load_trusted(path: str):
    """可信参考对手加载器（进程内执行；支持 .py、目录含 main.py、或 .zip）。"""
    p = os.path.abspath(path)
    cleanup_dir = None
    if os.path.isfile(p) and p.lower().endswith(".zip"):
        cleanup_dir = tempfile.mkdtemp(prefix="kagg_trusted_")
        pkg_dir = extract_archive(p, cleanup_dir)
        entry = os.path.join(pkg_dir, "main.py")
        sys_insert = pkg_dir
    elif os.path.isdir(p):
        entry = os.path.join(p, "main.py")
        if not os.path.isfile(entry):
            raise SandboxError(f"参考目录缺少 main.py：{p}")
        sys_insert = p
    else:
        entry = p
        sys_insert = None

    with open(entry, "r", encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType("trusted_agent")
    mod.__file__ = os.path.abspath(entry)
    ns = {"__name__": "trusted_agent", "__file__": mod.__file__}
    added = False
    if sys_insert and sys_insert not in sys.path:
        sys.path.insert(0, sys_insert)
        added = True
    try:
        exec(compile(src, entry, "exec"), ns)
        fn = _extract_entry_from_namespace(ns, "参考agent")
    finally:
        if added and sys_insert in sys.path:
            sys.path.remove(sys_insert)
    label = submission_label(path)

    def call(obs):
        return fn(obs)

    if cleanup_dir:
        call._cleanup_dir = cleanup_dir
    return label, call


class PolicyProcess:
    """子进程里的提交。按行交换 JSON；超时或进程退出后不再可用。"""

    def __init__(self, target_path: str, seed: int, cleanup_dir: str | None = None):
        self._cleanup_dir = cleanup_dir
        self.dead = False
        self.timeout = None
        env = {**os.environ, "PYTHONHASHSEED": "0"}
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-u", os.path.abspath(__file__), "--worker", os.path.abspath(target_path), str(seed)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1, env=env,
            )
            self.q: queue.Queue = queue.Queue()
            threading.Thread(target=_pump, args=(self.proc.stdout, self.q), daemon=True).start()
            reply = self._wait(LOAD_TIMEOUT)
            if reply is None or "load_error" in reply:
                self.close()
                raise SandboxError(reply["load_error"] if reply else "提交加载超时或进程退出")
        except Exception:
            self.close()
            raise

    def set_timeout(self, seconds):
        self.timeout = seconds

    def _wait(self, timeout):
        try:
            line = self.q.get(timeout=timeout)
        except queue.Empty:
            line = None
        if line is None:
            self.close()
            return None
        try:
            reply = json.loads(line)
        except (ValueError, RecursionError):
            return {"error": "回复无法解析"}
        return reply if isinstance(reply, dict) else {"error": "回复不是对象"}

    def __call__(self, observation):
        if self.dead:
            raise RuntimeError("策略进程已退出")
        try:
            self.proc.stdin.write(json.dumps(observation, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()
        except OSError:
            self.close()
            raise RuntimeError("策略进程已退出")
        limit = None if self.timeout is None else self.timeout + TIMEOUT_GRACE
        started = time.perf_counter()
        reply = self._wait(limit)
        if reply is None:
            over = limit is not None and time.perf_counter() - started >= limit
            raise TimeoutError("策略超时") if over else RuntimeError("策略进程已退出")
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply.get("action")

    def close(self):
        self.dead = True
        proc = getattr(self, "proc", None)
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
        cdir = getattr(self, "_cleanup_dir", None)
        if cdir:
            self._cleanup_dir = None
            shutil.rmtree(cdir, ignore_errors=True)

    def __del__(self):
        if not getattr(self, "dead", True) or getattr(self, "_cleanup_dir", None):
            self.close()


def _pump(stream, q):
    try:
        for line in stream:
            q.put(line)
    except Exception:
        pass
    q.put(None)


def load_agent(path: str):
    """启动独立子进程加载提交（支持 .zip 压缩包、含 main.py 的目录或 .py 单文件），返回 (label, callable)。"""
    abs_path = os.path.abspath(path)
    cleanup_dir = None
    if os.path.isdir(abs_path):
        if not os.path.isfile(os.path.join(abs_path, "main.py")):
            raise SandboxError(f"提交目录缺少入口文件 main.py：{path}")
        target = abs_path
    elif os.path.isfile(abs_path):
        if abs_path.lower().endswith(".zip"):
            cleanup_dir = tempfile.mkdtemp(prefix="kagg_pkg_")
            try:
                target = extract_archive(abs_path, cleanup_dir)
            except Exception:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
                raise
        elif abs_path.lower().endswith(".py"):
            target = abs_path
        else:
            raise SandboxError(f"不支持的提交文件类型（需要 .zip、含 main.py 的目录或 .py）：{path}")
    else:
        raise SandboxError(f"找不到提交路径：{path}")

    # 子进程种子来自调用方当前的随机状态，调用方先 random.seed 就能复现。
    fn = PolicyProcess(target, random.getrandbits(62), cleanup_dir=cleanup_dir)
    return submission_label(abs_path), fn


def _load_inprocess(path: str):
    abs_path = os.path.abspath(path)
    if os.path.isdir(abs_path):
        pkg_dir = abs_path
        entry = os.path.join(pkg_dir, "main.py")
        if not os.path.isfile(entry):
            raise SandboxError("提交目录缺少入口文件 main.py")
    elif os.path.isfile(abs_path) and abs_path.lower().endswith(".zip"):
        # 仅当 --worker 被直接传入 .zip 时兜底解压（正常路径由父进程提前解压为只读目录）
        pkg_dir = tempfile.mkdtemp(prefix="kagg_worker_")
        extract_archive(abs_path, pkg_dir)
        entry = os.path.join(pkg_dir, "main.py")
    elif os.path.isfile(abs_path):
        entry = abs_path
        pkg_dir = os.path.dirname(entry)
    else:
        raise SandboxError(f"找不到提交入口：{path}")

    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
    try:
        os.chdir(pkg_dir)
    except OSError:
        pass

    with open(entry, "r", encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType("submitted_agent")
    mod.__file__ = os.path.abspath(entry)
    ns = {
        "__name__": "submitted_agent",
        "__file__": mod.__file__,
        "__package__": "",
    }
    exec(compile(src, entry, "exec"), ns)
    return _extract_entry_from_namespace(ns, "提交入口")


class Attr(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


def _attr(v):
    if isinstance(v, dict):
        return Attr({k: _attr(x) for k, x in v.items()})
    if isinstance(v, list):
        return [_attr(x) for x in v]
    return v


def _worker(path: str, seed: int):
    """子进程入口。协议走复制出来的 stdout，提交里的 print 进空设备。"""
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    sys.stdin.reconfigure(encoding="utf-8")

    def send(obj):
        out.write(json.dumps(obj, separators=(",", ":")) + "\n")
        out.flush()

    random.seed(seed)
    try:
        fn = _load_inprocess(path)
    except Exception as e:
        send({"load_error": "%s: %s" % (type(e).__name__, e)})
        return
    random.seed(seed)
    send({"ok": True})
    for line in sys.stdin:
        try:
            send({"action": fn(_attr(json.loads(line)))})
        except Exception as e:
            send({"error": "%s: %s" % (type(e).__name__, e)})


if __name__ == "__main__" and sys.argv[1:2] == ["--worker"]:
    _worker(sys.argv[2], int(sys.argv[3]))
