#!/usr/bin/env python3
"""Run a program with task-only mounts, disabled socket syscalls and a wall cap."""
import argparse, ctypes, errno, os, pathlib, subprocess, tempfile
p=argparse.ArgumentParser()
p.add_argument('--root',required=True)
p.add_argument('--time',type=float,default=20)
p.add_argument('--address-space',type=int,default=2147483648)
p.add_argument('cmd',nargs=argparse.REMAINDER)
a=p.parse_args()
root=pathlib.Path(a.root).resolve(strict=True)
if not root.is_dir() or not a.cmd or a.cmd[0]!='--':
    p.error('require --root task_dir -- command ...')
lib=ctypes.CDLL('libseccomp.so.2')
lib.seccomp_init.argtypes=[ctypes.c_uint32]; lib.seccomp_init.restype=ctypes.c_void_p
lib.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
lib.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p]; lib.seccomp_export_bpf.argtypes=[ctypes.c_void_p,ctypes.c_int]
lib.seccomp_release.argtypes=[ctypes.c_void_p]
ctx=lib.seccomp_init(0x7fff0000)
if not ctx: raise RuntimeError('seccomp init')
with tempfile.TemporaryFile() as f:
    for name in ('socket','socketpair','connect','bind','listen','accept','accept4','sendto','recvfrom','sendmsg','recvmsg','sendmmsg','recvmmsg','io_uring_setup','io_uring_enter','io_uring_register','ptrace','process_vm_readv','process_vm_writev','setns','unshare','mount','bpf'):
        n=lib.seccomp_syscall_resolve_name(name.encode())
        if n<0 or lib.seccomp_rule_add(ctx,0x00050000|errno.EPERM,n,0)!=0:
            raise RuntimeError(f'cannot filter {name}')
    if lib.seccomp_export_bpf(ctx,f.fileno())!=0: raise RuntimeError('seccomp export')
    lib.seccomp_release(ctx)
    f.seek(0)
    cmd=['bwrap','--unshare-user','--unshare-pid','--unshare-ipc','--new-session','--die-with-parent','--clearenv','--unshare-uts','--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--bind',str(root),'/work','--chdir','/work','--setenv','PATH','/usr/bin:/bin','--setenv','LC_ALL','C.UTF-8','--setenv','HOME','/tmp','--setenv','PYTHONDONTWRITEBYTECODE','1','--seccomp',str(f.fileno()),'--','/usr/bin/prlimit',f'--as={a.address_space}','--fsize=33554432','--nofile=256','--']+a.cmd[1:]
    try: raise SystemExit(subprocess.run(cmd,timeout=a.time,pass_fds=(f.fileno(),)).returncode)
    except subprocess.TimeoutExpired: raise SystemExit(124)
