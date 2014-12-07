import fcntl
import functools
import os

import nose.tools as nt

from .. import filecontrol


def with_fd(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        r, w = os.pipe()
        try:
            return func(r, *args, **kwargs)
        finally:
            os.close(r)
            os.close(w)
    return wrapper


@with_fd
def test_non_blocking(fd):
    nt.assert_equals(0, filecontrol.set_non_blocking(fd))
    nt.assert_true(os.O_NONBLOCK & fcntl.fcntl(fd, fcntl.F_GETFL))


@with_fd
def test_blocking(fd):
    nt.assert_equals(0, filecontrol.set_non_blocking(fd, False))
    nt.assert_false(os.O_NONBLOCK & fcntl.fcntl(fd, fcntl.F_GETFL))


@with_fd
def test_close_on_exec(fd):
    nt.assert_equals(0, filecontrol.set_close_on_exec(fd))
    nt.assert_true(fcntl.FD_CLOEXEC & fcntl.fcntl(fd, fcntl.F_GETFD))


@with_fd
def test_no_close_on_exec(fd):
    nt.assert_equals(0, filecontrol.set_close_on_exec(fd, False))
    nt.assert_false(fcntl.FD_CLOEXEC & fcntl.fcntl(fd, fcntl.F_GETFD))
