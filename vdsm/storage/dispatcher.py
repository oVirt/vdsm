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

import traceback
import logging
from config import config

import task
import resourceManager
import hsm
import storage_exception as se
from threadLocal import vars
from storageConstants import STORAGE


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
            except Exception, e:
                self.log.error(e)
                self.log.error(traceback.format_exc())
                exceptionObj = ctask.defaultException
                if exceptionObj and hasattr(exceptionObj, "response"):
                    return exceptionObj.response()
                return se.GeneralException("method %s, error: %s" % (str(self.name), str(e))).response()
            except:
                self.log.error(traceback.format_exc())
                exceptionObj = ctask.defaultException
                if exceptionObj and hasattr(exceptionObj, "response"):
                    return exceptionObj.response()
                return se.GeneralException("method %s" % (str(self.name))).response()
        except:
            try:
                try:
                    self.log.error("Unhandled exception in run and protect: %s, args: %s " % (str(self.name), str(args)))
                finally:
                    self.log.error(traceback.format_exc())
            finally:
                return self.STATUS_ERROR.copy()



class StorageDispatcher:
    log = logging.getLogger('Storage.Dispatcher')

    def __init__(self):
        self.storage_repository = config.get('irs', 'repository')
        resourceManager.ResourceManager.getInstance().registerNamespace(STORAGE, resourceManager.SimpleResourceFactory())
        self.hsm = hsm.HSM()
        self.spm = self.hsm.spm
        self._init_public_functions()
        self.log.info("Starting StorageDispatcher...")


    def _exposeFunctions(self, obj, prefix):
        for funcName in dir(obj):
            funcObj = getattr(obj, funcName)
            if funcName.startswith(prefix) and callable(funcObj):
                publicName = funcName[len(prefix):]
                if hasattr(self, publicName):
                    self.log.error("StorageDispatcher: init - multiple public functions with same name: %s" % publicName)
                    continue
                # Create a new entry in instance's "dict" that will mask the original method
                self.__dict__[publicName] = Protect(funcObj, publicName).run


    def _init_public_functions(self):
        """ Generate and expose protected functions """
        privatePrefix = "_" + self.__class__.__name__ + "__do_"
        publicPrefix = "public_"
        self._exposeFunctions(self.hsm, publicPrefix)
        self._exposeFunctions(self, privatePrefix)
        self._exposeFunctions(self.spm, publicPrefix)


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
