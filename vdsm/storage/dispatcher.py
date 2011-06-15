#
# Copyright 2009-2010 Red Hat, Inc. All rights reserved.
# Use is subject to license terms.
#

import traceback
import inspect
import logging
import types
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

    def __init__(self, func, name, loggableArgsFunc=None, loggableRespFunc=None):
        self.name = name
        self.func = func
        if loggableArgsFunc:
            self.loggableArgs = loggableArgsFunc
        if loggableRespFunc:
            self.loggableResp = loggableRespFunc
        try:
            self.argNames = func.im_self.innerArgNames
        except:
            self.argNames, args, kwargs, defValues = inspect.getargspec(func)
        if isinstance(func, types.MethodType):
            del self.argNames[0]

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


    def loggableArgs(self, *args, **kwargs):
        s = ""
        numArgs = len(args)
        if numArgs <= len(self.argNames):
            for i in range(numArgs):
                s += " %s=%s" % (self.argNames[i], str(args[i]))
        if kwargs:
            s += " " + str(kwargs)
        return s

    def loggableResp(self, resp):
        return str(resp)

    def run(self, *rawArgs, **rawKwargs):
        try:
            ctask = task.Task(id=None, name=self.name)
            vars.task = ctask
            # Make sure all arguments are non unicode.
            # TODO : Support unicode
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

            try:
                s = self.loggableArgs(*args, **kwargs)
                self.log.info("Run and protect: %s, args: (%s)" % (str(self.name), s))
                response = self.STATUS_OK.copy()
                result = ctask.prepare(self.func, *args, **kwargs)
                if type(result) == dict:
                    response.update(result)
                s = self.loggableResp(response)
                self.log.info("Run and protect: %s, Return response: %s" % (str(self.name), s))
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
                return se.GeneralException("method %s args: (%s), error: %s" % (str(self.name), s, str(e))).response()
            except:
                self.log.error(traceback.format_exc())
                exceptionObj = ctask.defaultException
                if exceptionObj and hasattr(exceptionObj, "response"):
                    return exceptionObj.response()
                return se.GeneralException("method %s args: (%s)" % (str(self.name), s)).response()
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
                argsFuncName = "_log_" + publicName
                respFuncName = "_logResp_" + publicName
                argsFunc = respFunc = None
                if hasattr(obj, argsFuncName):
                    argsFunc = getattr(obj, argsFuncName)
                if hasattr(obj, respFuncName):
                    respFunc = getattr(obj, respFuncName)
                self.__dict__[publicName] = Protect(funcObj, publicName, argsFunc, respFunc).run


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
