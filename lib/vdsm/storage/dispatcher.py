# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import logging
from functools import wraps

from vdsm.storage import exception as se
from vdsm.storage import task


_EXPORTED_ATTRIBUTE = "__dispatcher_exported__"


def exported(f):
    setattr(f, _EXPORTED_ATTRIBUTE, True)
    return f


class Dispatcher(object):
    log = logging.getLogger('storage.dispatcher')

    STATUS_OK = {'status': {'code': 0, 'message': "OK"}}
    STATUS_ERROR = {'status': {'code': 100, 'message': "ERROR"}}

    def __init__(self, obj):
        self._obj = obj
        self._exposeFunctions(obj)
        self.log.info("Starting StorageDispatcher...")

    @property
    def ready(self):
        return getattr(self._obj, 'ready', True)

    def _exposeFunctions(self, obj):
        for funcName in dir(obj):
            if funcName.startswith("_"):
                continue
            funcObj = getattr(obj, funcName)
            if hasattr(funcObj, _EXPORTED_ATTRIBUTE) and callable(funcObj):
                if hasattr(self, funcName):
                    self.log.error("StorageDispatcher: init - multiple public"
                                   " functions with same name: %s" % funcName)
                    continue
                # Create a new entry in instance's "dict" that will mask the
                # original method
                setattr(self, funcName, self.protect(funcObj, funcName))

    def protect(self, func, name, *args, **kwargs):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                ctask = task.Task(id=None, name=name)
                try:
                    response = self.STATUS_OK.copy()
                    result = ctask.prepare(func, *args, **kwargs)
                    if type(result) == dict:
                        response.update(result)
                    return response
                except se.GeneralException as e:
                    # Match api.method format
                    if e.expected:
                        self.log.info("FINISH %s error=%s", name, e)
                    else:
                        self.log.error("FINISH %s error=%s", name, e)
                    return e.response()
                except BaseException as e:
                    # Match api.method format
                    self.log.exception("FINISH %s error=%s", name, e)
                    defaultException = ctask.defaultException
                    if (defaultException and
                            hasattr(defaultException, "response")):
                        resp = defaultException.response()
                        defaultExceptionInfo = (resp['status']['code'],
                                                resp['status']['message'])
                        return se.generateResponse(e, defaultExceptionInfo)

                    return se.generateResponse(e)
            except:
                try:
                    # We should never reach this
                    self.log.exception(
                        "Unhandled exception (name=%s, args=%s, kwargs=%s)",
                        name, args, kwargs)
                finally:
                    return self.STATUS_ERROR.copy()

        return wrapper
