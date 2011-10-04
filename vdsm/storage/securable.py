from threading import Event
from functools import wraps

class SecureError(RuntimeError): pass

class Securable(type):
    def __new__(mcs, name, bases, fdict):

        def _isSafe(self):
            return self._safety.isSet()

        def _setSafe(self):
            self._safety.set()

        def _setUnsafe(self):
            self._safety.clear()

        fdict['__securable__'] = True
        fdict['_safety'] = Event()
        fdict['_isSafe'] = _isSafe
        fdict['_setSafe'] = _setSafe
        fdict['_setUnsafe'] = _setUnsafe
        return type.__new__(mcs, name, bases, fdict)

OVERRIDE_ARG = "__securityOverride"

def secured(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not hasattr(args[0], "__securable__"):
            raise RuntimeError("Secured object is not a securable")

        override = kwargs.get(OVERRIDE_ARG, False)
        try:
            del kwargs[OVERRIDE_ARG]
        except KeyError:
            pass

        if not (args[0]._isSafe() or override):
            raise SecureError()

        return f(*args, **kwargs)
    return wrapper


