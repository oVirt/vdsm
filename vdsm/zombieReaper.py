import os
import signal

_trackedPids = set()


def autoReapPID(pid):
    _trackedPids.add(pid)
    # SIGCHLD happend before we added the pid to the set
    _tryReap(pid)


def _tryReap(pid):
        try:
            pid, rv = os.waitpid(pid, os.WNOHANG)
            if pid != 0:
                _trackedPids.discard(pid)
        except OSError:
            _trackedPids.discard(pid)


def _zombieReaper(signum, frame):
    for pid in _trackedPids.copy():
        _tryReap(pid)


def registerSignalHandler():
    signal.signal(signal.SIGCHLD, _zombieReaper)


def unregisterSignalHandler():
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
