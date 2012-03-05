#
# Copyright 2009-2011 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import logging
from config import config

import task
import storage_exception as se
from threadLocal import vars

_EXPORTED_ATTRIBUTE = "__dispatcher_exported__"

class Protect:
    STATUS_OK = {'status': {'code': 0, 'message': "OK"}}
    STATUS_ERROR = {'status': {'code': 100, 'message': "ERROR"}}
    log = logging.getLogger('Storage.Dispatcher.Protect')

    def __init__(self, func, name):
        self.name = name
        self.func = func

        self.help = None
        try:
            if hasattr(func.im_self, "help"):
                self.help = getattr(func.im_self, "help")
        except:
            pass
        if not self.help:
            try:
                self.help = getattr(func, "__doc__")
            except:
                pass
        if not self.help:
            self.help = "No help available for method %s" % name

    def convertUnicodeArgs(self, rawArgs, rawKwargs):
        # Make sure all arguments do not contain non ASCII chars
        args = [None] * len(rawArgs)
        kwargs = {}
        try:
            for i in range(len(rawArgs)):
                if isinstance(rawArgs[i], unicode):
                    args[i] = str(rawArgs[i])
                else:
                    args[i] = rawArgs[i]

            for i in rawKwargs:
                if isinstance(rawKwargs[i], unicode):
                    kwargs[i] = str(rawKwargs[i])
                else:
                    kwargs[i] = rawKwargs[i]

        except UnicodeEncodeError, e:
            self.log.error(e)
            return se.UnicodeArgumentException().response()

        return args, kwargs

    def run(self, *args, **kwargs):
        try:
            # TODO : Support unicode
            args, kwargs = self.convertUnicodeArgs(args, kwargs)
            ctask = task.Task(id=None, name=self.name)
            vars.task = ctask
            try:
                response = self.STATUS_OK.copy()
                result = ctask.prepare(self.func, *args, **kwargs)
                if type(result) == dict:
                    response.update(result)
                return response
            except se.GeneralException, e:
                self.log.error(e.response())
                return e.response()
            except BaseException, e:
                self.log.error(e, exc_info=True)
                defaultException = ctask.defaultException
                if defaultException and hasattr(defaultException, "response"):
                    resp = defaultException.response()
                    defaultExceptionInfo = (resp['status']['code'], resp['status']['message'])
                    return se.generateResponse(e, defaultExceptionInfo)

                return se.generateResponse(e)
        except:
            try:
                self.log.error("Unhandled exception in run and protect: %s, "
                        "args: %s ", self.name, args, exc_info=True)
            finally:
                return self.STATUS_ERROR.copy()


def exported(f):
    setattr(f, _EXPORTED_ATTRIBUTE, True)
    return f

class Dispatcher:
    log = logging.getLogger('Storage.Dispatcher')

    def __init__(self, obj):
        self.storage_repository = config.get('irs', 'repository')
        self._exposeFunctions(obj)
        self.log.info("Starting StorageDispatcher...")


    def _exposeFunctions(self, obj):
        for funcName in dir(obj):
            funcObj = getattr(obj, funcName)
            if hasattr(funcObj, _EXPORTED_ATTRIBUTE) and callable(funcObj):
                if hasattr(self, funcName):
                    self.log.error("StorageDispatcher: init - multiple public functions with same name: %s" % funcName)
                    continue
                # Create a new entry in instance's "dict" that will mask the original method
                self.__dict__[funcName] = Protect(funcObj, funcName).run


    def _methodHelp(self, method):
        # this method must be present for system.methodHelp
        # to work
        help = "No help available for method %s" % method
        try:
            if hasattr(self, method):
                help = getattr(self, method).im_self.help
        except:
            pass
        return help
