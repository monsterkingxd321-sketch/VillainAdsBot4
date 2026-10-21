"""
A per session lock.

WHY THIS EXISTS
The bot can be started more than once - on the VPS and on the PC, or twice
by accident. When two instances use the SAME session file at the same time,
Telegram kills the session with:

    AuthKeyDuplicatedError: The authorization key (session file) was used
    under two different IP addresses simultaneously, and can no longer be
    used.

That error PERMANENTLY destroys the account - the user has to log in again.
This lock makes sure that a session is only ever used by ONE process.
"""

import errno
import os
import time

LOCK_DIR = "user_data/.locks"


def _lock_path(session_id: str) -> str:
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "+-_")
    return os.path.join(LOCK_DIR, f"{safe}.lock")


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    except Exception:
        return False


def acquire(session_id: str) -> bool:
    """
    Tries to take the lock for a session.

    Returns True when the lock was taken (the session is ours),
    False when another live process is already using it.
    """
    try:
        os.makedirs(LOCK_DIR, exist_ok=True)
    except Exception:
        return True     # never block the bot because of a lock problem

    path = _lock_path(session_id)

    for _attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return True
        except FileExistsError:
            # The lock exists - is the owner still alive?
            pid = 0
            try:
                with open(path, "r") as f:
                    pid = int((f.read() or "0").strip() or 0)
            except Exception:
                pid = 0
            if pid and pid != os.getpid() and _pid_alive(pid):
                return False       # another live instance owns it
            # A stale lock (the owner died) - take it over.
            try:
                os.remove(path)
            except OSError:
                pass
        except OSError as e:
            if e.errno == errno.EEXIST:
                return False
            return True
        except Exception:
            return True
    return False


def release(session_id: str):
    """Releases the lock of a session."""
    try:
        path = _lock_path(session_id)
        with open(path, "r") as f:
            pid = int((f.read() or "0").strip() or 0)
        if pid and pid != os.getpid():
            return          # it belongs to another process
        os.remove(path)
    except Exception:
        pass


def is_locked_elsewhere(session_id: str) -> bool:
    """Returns True when another LIVE process is using this session."""
    try:
        path = _lock_path(session_id)
        with open(path, "r") as f:
            pid = int((f.read() or "0").strip() or 0)
        return bool(pid) and pid != os.getpid() and _pid_alive(pid)
    except Exception:
        return False


def list_locks() -> dict:
    """Returns {session_id: pid} of every active lock."""
    locks = {}
    try:
        for name in os.listdir(LOCK_DIR):
            if not name.endswith(".lock"):
                continue
            path = os.path.join(LOCK_DIR, name)
            try:
                with open(path, "r") as f:
                    pid = int((f.read() or "0").strip() or 0)
            except Exception:
                continue
            if pid and _pid_alive(pid):
                locks[name[:-5]] = pid
    except Exception:
        pass
    return locks
