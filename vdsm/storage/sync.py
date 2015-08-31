from threading import Event
from functools import wraps
from vdsm import concurrent


def AsyncCallStub(result):
    def stubby():
        return result

    return AsyncCall(stubby, [], [])


class AsyncCallNotDone(RuntimeError):
    pass


class AsyncCall(object):
    def __init__(self, f, args, kwargs):
        self._event = Event()
        self._result = None
        self._callable = f
        self._args = args
        self._kwargs = kwargs

    def wait(self, timeout=None):
        self._event.wait(timeout)
        return self._event.isSet()

    def result(self):
        if self._result is None:
            return AsyncCallNotDone()

        return self._result

    def _wrapper(self):
        res = err = None
        try:
            res = self._callable(*self._args, **self._kwargs)
        except Exception as e:
            err = e
        finally:
            self._result = (res, err)
            self._event.set()

    def _call(self):
        t = concurrent.thread(self._wrapper)
        t.start()


def asyncmethod(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        acall = AsyncCall(f, args, kwargs)
        acall._call()

        return acall

    return wrapper
