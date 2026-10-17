"""Kernel semantics probe: PR_SET_PDEATHSIG follows the FORKING THREAD, not the process.
usage: python3.11 -I -S -B -u pdeath_probe.py main_exits|forker_thread_exits"""
import ctypes
import os
import sys
import threading
import time

libc = ctypes.CDLL(None, use_errno=True)


def fork_child():
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        libc.prctl(1, 9, 0, 0, 0)  # PR_SET_PDEATHSIG, SIGKILL
        os.write(w, b"r")  # armed: the forking thread may now exit
        time.sleep(30)
        os._exit(0)
    os.close(w)
    os.read(r, 1)
    os.close(r)
    return pid


mode = sys.argv[1]
if mode == "main_exits":
    pid = fork_child()
    sys.stdout.write("%d %d\n" % (os.getpid(), pid))
    sys.stdout.flush()
    t = threading.Thread(target=time.sleep, args=(6,))
    t.start()
    libc.pthread_exit(None)  # the main thread ends; the process lives on in the other thread
else:
    box = []
    t = threading.Thread(target=lambda: box.append(fork_child()))
    t.start()
    t.join()
    sys.stdout.write("%d %d\n" % (os.getpid(), box[0]))
    sys.stdout.flush()
    time.sleep(6)
