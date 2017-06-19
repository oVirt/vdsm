#
# Copyright 2011-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import threading
import logging
import re
import weakref
from functools import partial
from contextlib import nested
from uuid import uuid4

from six.moves import queue

from vdsm import utils
from vdsm.common import concurrent
from vdsm.common.logutils import SimpleLogAdapter
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import rwlock


# Errors

class ResourceManagerError(Exception):
    pass


class NamespaceRegistered(ResourceManagerError):
    """ Raised if a namesapce is already registered """


class RequestAlreadyProcessedError(ResourceManagerError):
    pass


class RequestTimedOutError(ResourceManagerError):
    pass

# TODO : Consider changing when we decided on a unified way of representing
#        enums.


SHARED = "shared"
EXCLUSIVE = "exclusive"


class LockState:
    free = "free"
    shared = "shared"
    locked = "locked"

    def __init__(self, state=free):
        self.validate(state)
        self.state = state

    def __str__(self):
        return self.state

    def __eq__(self, x):
        if type(x) == str:
            return self.state == x
        if isinstance(x, self):
            return x.state == self.state

    def __ne__(self, x):
        return not self.__eq__(x)

    @classmethod
    def fromType(cls, locktype):
        if str(locktype) == SHARED:
            return cls.shared
        if str(locktype) == EXCLUSIVE:
            return cls.locked
        raise ValueError("invalid locktype %s" % locktype)

    @classmethod
    def validate(cls, state):
        try:
            if type(getattr(cls, state)) != str:
                raise ValueError
        except:
            raise ValueError("invalid lock state %s" % state)


# TODO : Integrate all factory functionality to manager
class SimpleResourceFactory(object):
    """
    A resource factory that does nothing. Can be used when nothing is enough.

    .. note:
        except for `resourceExists` nothing is used at the moment
    """
    def resourceExists(self, resourceName):
        """
        Return :keyword:`True` if a resource with that name is producible with
        this factory.
        """
        return True

    def createResource(self, resourceName, lockType):
        """
        Called *before* the first incref. (Refcount is 0)
        Returns the resource or None if the resource has no real
        implementation.

        The object needs to have a `close()` method if it wishes to be
        release by the resource manager

        All methods except `close()` will be available to the user.
        """
        return None


class RequestRef(object):
    """
    The request object that the user can interact with.
    Makes sure that the user does only what he is allowed to.
    """

    _exposeInternalAttributes = ["canceled", "cancel", "namespace", "name",
                                 "fullName", "lockType", "status", "granted",
                                 "wait"]

    def __getattr__(self, name):
        if name in self._exposeInternalAttributes:
            return getattr(self._realRequset, name)

        raise AttributeError(name)

    def __init__(self, realRequset):
        self._realRequset = realRequset

    def __str__(self):
        return self._realRequset.__str__()

    def __eq__(self, other):
        if not isinstance(other, RequestRef):
            return False

        return (self._realRequset == other._realRequset)


class Request(object):
    """
    Internal request object, don't use directly
    """
    _log = logging.getLogger("storage.ResourceManager.Request")
    namespace = property(lambda self: self._namespace)
    name = property(lambda self: self._name)
    fullName = property(lambda self: "%s.%s" % (self._namespace, self._name))
    lockType = property(lambda self: self._lockType)
    syncRoot = property(lambda self: self._syncRoot)

    def __init__(self, namespace, name, lockType, callback):
        self._syncRoot = threading.RLock()
        self._namespace = namespace
        self._name = name
        self._lockType = lockType
        self._isActive = True
        self._isCanceled = False
        self._doneEvent = threading.Event()
        self._callback = callback
        self.reqID = str(uuid4())
        self._log = SimpleLogAdapter(self._log, {"ResName": self.fullName,
                                                 "ReqID": self.reqID})

    def cancel(self):
        with self._syncRoot:
            if not self._isActive:
                self._log.warn("Tried to cancel a processed request")
                raise RequestAlreadyProcessedError("Cannot cancel a processed "
                                                   "request")

            self._isActive = False
            self._isCanceled = True

            self._log.debug("Canceled request")
            try:
                self._callback(RequestRef(self), None)
            except Exception:
                self._log.warn("Request callback threw an exception",
                               exc_info=True)
            self._callback = None
            self._doneEvent.set()

    def _status(self):
        with self._syncRoot:
            if self._isCanceled:
                return "canceled"
            if self._doneEvent.isSet():
                return "granted"
            return "waiting"

    def canceled(self):
        return self._isCanceled

    def grant(self):
        with self._syncRoot:
            if not self._isActive:
                self._log.warn("Tried to grant a processed request")
                raise RequestAlreadyProcessedError("Cannot grant a processed "
                                                   "request")

            self._isActive = False
            self._log.debug("Granted request")
            self._doneEvent.set()

    def emit(self, resource):
        try:
            ref = RequestRef(self)
            self._callback(ref, resource)
        except Exception:
            self._log.warn("Request callback threw an exception",
                           exc_info=True)

    def wait(self, timeout=None):
        return self._doneEvent.wait(timeout)

    def granted(self):
        with self._syncRoot:
            return (not self._isCanceled) and self._doneEvent.isSet()

    def __str__(self):
        return "Request for %s - %s: %s" % (self.fullName, self.lockType,
                                            self._status())


class ResourceRef(object):
    """
    A reference to a resource. Can be used to conveniently modify an owned
    resource.

    This object will auto release the referenced resource unless autorelease
    is set to `False`
    """
    _log = logging.getLogger("storage.ResourceManager.ResourceRef")
    namespace = property(lambda self: self._namespace)
    name = property(lambda self: self._name)
    fullName = property(lambda self: "%s.%s" % (self._namespace, self._name))

    # States whether this reference is pointing to an owned reference
    isValid = property(lambda self: self._isValid)

    def __init__(self, namespace, name, wrappedObject=None,
                 resRefID=str(uuid4())):
        self._namespace = namespace
        self._name = name
        self._log = SimpleLogAdapter(self._log, {"ResName": self.fullName,
                                                 "ResRefID": resRefID})

        self.__wrappedObject = wrappedObject
        if wrappedObject is not None:
            self.__wrapObj()

        self.autoRelease = True
        self._isValid = True
        self._syncRoot = rwlock.RWLock()

    def __wrapObj(self):
        for attr in dir(self.__wrappedObject):
            if hasattr(self, attr) or attr in ('close', 'switchLockType'):
                continue

            weakmethod = partial(ResourceRef.__methodProxy,
                                 weakref.proxy(self), attr)
            setattr(self, attr, weakmethod)

    def __methodProxy(self, attr, *args, **kwargs):
        with self._syncRoot.shared:
            if not self.isValid:
                raise se.ResourceReferenceInvalid

            return getattr(self.__wrappedObject, attr)(*args, **kwargs)

    def release(self):
        with self._syncRoot.exclusive:
            self.__wrappedObject = None
            if not self._isValid:
                self._log.warn("Tried to re-release a resource. Request "
                               "ignored.")
                return

            releaseResource(self.namespace, self.name)
            self._isValid = False

    def getStatus(self):
        return _getResourceStatus(self.namespace, self.name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()

    def __del__(self):
        if self._isValid and self.autoRelease:
            def release(log, namespace, name):
                log.warn("Resource reference was not properly released. "
                         "Autoreleasing.")
                # In Python, objects are refcounted and are deleted immediately
                # when the last reference is freed. This means the __del__
                # method can be called inside of any context. The
                # releaseResource method we use tries to acquire locks. So we
                # might try to acquire the lock in a locked context and reach a
                # deadlock. This is why I need to use a timer. It will defer
                # the operation and use a different context.
                releaseResource(namespace, name)
            t = concurrent.thread(
                release,
                args=(self._log, self.namespace, self.name),
                name="rm/" + self.name[:8])
            t.start()
            self._isValid = False

    def __repr__(self):
        return "< ResourceRef '%s', isValid: '%s' obj: '%s'>" % (
            self.fullName, self.isValid,
            repr(self.__wrappedObject) if self.isValid else None)


class _ResourceManager(object):
    """
    Manages all the resources in the application.

    This class is for internal usage only, clients should use the module
    interface.
    """
    _log = logging.getLogger("storage.ResourceManager")
    _namespaceValidator = re.compile(r"^[\w\d_-]+$")
    _resourceNameValidator = re.compile(r"^[^\s.]+$")

    def __init__(self):
        self._syncRoot = rwlock.RWLock()
        self._namespaces = {}

    def registerNamespace(self, namespace, factory):
        if not self._namespaceValidator.match(namespace):
            raise ValueError("Illegal namespace '%s'" % namespace)

        if namespace in self._namespaces:
            raise NamespaceRegistered("Namespace '%s' already registered"
                                      % namespace)

        with self._syncRoot.exclusive:
            if namespace in self._namespaces:
                raise NamespaceRegistered("Namespace '%s' already registered"
                                          % namespace)

            self._log.debug("Registering namespace '%s'", namespace)

            self._namespaces[namespace] = Namespace(factory)

    def unregisterNamespace(self, namespace):
        with self._syncRoot.exclusive:
            if namespace not in self._namespaces:
                raise KeyError("Namespace '%s' doesn't exist" % namespace)

            self._unregisterNamespaceLocked(namespace)

    def _unregisterNamespaceLocked(self, namespace):
        """
        Must be called when holding self._syncRoot.exclusive, and namespace
        exists in self._namespaces.
        """
        self._log.debug("Unregistering namespace '%s'", namespace)
        namespaceObj = self._namespaces[namespace]
        with namespaceObj.lock:
            if len(namespaceObj.resources) > 0:
                raise ResourceManagerError("Cannot unregister Resource "
                                           "Factory '%s'. It has active "
                                           "resources." % (namespace))

            del self._namespaces[namespace]

    def getResourceStatus(self, namespace, name):
        if not self._resourceNameValidator.match(name):
            raise ValueError("Invalid resource name '%s'" % name)

        with self._syncRoot.shared:
            try:
                namespaceObj = self._namespaces[namespace]
            except KeyError:
                raise ValueError("Namespace '%s' is not registered with this "
                                 "manager" % namespace)
            resources = namespaceObj.resources
            with namespaceObj.lock:
                if not namespaceObj.factory.resourceExists(name):
                    raise KeyError("No such resource '%s.%s'" % (namespace,
                                                                 name))

                if name not in resources:
                    return LockState.free

                return LockState.fromType(resources[name].currentLock)

    def _switchLockType(self, resourceInfo, newLockType):
        switchLock = (resourceInfo.currentLock != newLockType)
        resourceInfo.currentLock = newLockType

        if resourceInfo.realObj is None:
            return

        if switchLock:
            if hasattr(resourceInfo.realObj, "switchLockType"):
                try:
                    resourceInfo.realObj.switchLockType(newLockType)
                    return
                except:
                    self._log.warn("Lock type switch failed on resource '%s'. "
                                   "Falling back to object recreation.",
                                   resourceInfo.fullName, exc_info=True)

            # If the resource can't switch we just release it and create it
            # again under a different locktype
            self._freeResource(resourceInfo)
            namespace = self._namespaces[resourceInfo.namespace]
            resourceInfo.realObj = namespace.factory.createResource(
                resourceInfo.name, resourceInfo.currentLock)

    def _freeResource(self, resourceInfo):
        if (resourceInfo.realObj is not None) and \
                (hasattr(resourceInfo.realObj, 'close')):
            try:
                resourceInfo.realObj.close()
            except:
                self._log.warn("Couldn't close resource '%s'.",
                               resourceInfo.fullName, exc_info=True)

    def acquireResource(self, namespace, name, lockType, timeout=None):
        """
        Acquire a resource synchronously.

        :returns: a reference to the resource.
        """
        if timeout is not None:
            try:
                timeout = int(timeout)
            except ValueError:
                raise TypeError("'timeout' must be number")

        resource = queue.Queue()

        def callback(req, res):
            resource.put(res)

        request = self.registerResource(namespace, name, lockType, callback)
        request.wait(timeout)
        if not request.granted():
            try:
                request.cancel()
                raise RequestTimedOutError("Request timed out. Could not "
                                           "acquire resource '%s.%s'" %
                                           (namespace, name))
            except RequestAlreadyProcessedError:
                # We might have acquired the resource between 'wait' and
                # 'cancel'
                if request.canceled():
                    raise se.ResourceAcqusitionFailed()

        return resource.get()

    def registerResource(self, namespace, name, lockType, callback):
        """
        Register to acquire a resource asynchronously.

        :returns: a request object that tracks the current request.
        """
        fullName = "%s.%s" % (namespace, name)

        if not self._resourceNameValidator.match(name):
            raise ValueError("Invalid resource name '%s'" % name)

        if lockType not in (SHARED, EXCLUSIVE):
            raise ValueError("invalid lock type %r" % lockType)

        request = Request(namespace, name, lockType, callback)
        self._log.debug("Trying to register resource '%s' for lock type '%s'",
                        fullName, lockType)
        with nested(utils.RollbackContext(),
                    self._syncRoot.shared) as (contextCleanup, lock):
            try:
                namespaceObj = self._namespaces[namespace]
            except KeyError:
                raise ValueError("Namespace '%s' is not registered with this "
                                 "manager" % namespace)

            resources = namespaceObj.resources
            with namespaceObj.lock:
                try:
                    resource = resources[name]
                except KeyError:
                    if not namespaceObj.factory.resourceExists(name):
                        raise KeyError("No such resource '%s'" % (fullName))
                else:
                    if len(resource.queue) == 0 and \
                            resource.currentLock == SHARED and \
                            request.lockType == SHARED:
                        resource.activeUsers += 1
                        self._log.debug("Resource '%s' found in shared state "
                                        "and queue is empty, Joining current "
                                        "shared lock (%d active users)",
                                        fullName, resource.activeUsers)
                        request.grant()
                        contextCleanup.defer(request.emit,
                                             ResourceRef(namespace, name,
                                                         resource.realObj,
                                                         request.reqID))
                        return RequestRef(request)

                    resource.queue.insert(0, request)
                    self._log.debug("Resource '%s' is currently locked, "
                                    "Entering queue (%d in queue)",
                                    fullName, len(resource.queue))
                    return RequestRef(request)

                # TODO : Creating the object inside the namespace lock causes
                #        the entire namespace to lock and might cause
                #        performance issues. As this is no currently a problem
                #        I left it as it is to keep the code simple. If there
                #        is a bottleneck in the resource framework, its
                #        probably here.
                try:
                    obj = namespaceObj.factory.createResource(name, lockType)
                except:
                    self._log.warn("Resource factory failed to create resource"
                                   " '%s'. Canceling request.", fullName,
                                   exc_info=True)
                    contextCleanup.defer(request.cancel)
                    return RequestRef(request)

                resource = resources[name] = ResourceInfo(obj, namespace, name)
                resource.currentLock = request.lockType
                resource.activeUsers += 1

                self._log.debug("Resource '%s' is free. Now locking as '%s' "
                                "(1 active user)", fullName, request.lockType)
                request.grant()
                contextCleanup.defer(request.emit,
                                     ResourceRef(namespace, name,
                                                 resource.realObj,
                                                 request.reqID))
                return RequestRef(request)

    def releaseResource(self, namespace, name):
        # WARN : unlike in resource acquire the user now has the request
        #        object and can CANCEL THE REQUEST at any time. Always use
        #        request.grant between try and except to properly handle such
        #        a case
        fullName = "%s.%s" % (namespace, name)

        self._log.debug("Trying to release resource '%s'", fullName)
        with nested(utils.RollbackContext(),
                    self._syncRoot.shared) as (contextCleanup, lock):
            try:
                namespaceObj = self._namespaces[namespace]
            except KeyError:
                raise ValueError("Namespace '%s' is not registered with this "
                                 "manager", namespace)
            resources = namespaceObj.resources

            with namespaceObj.lock:
                try:
                    resource = resources[name]
                except KeyError:
                    raise ValueError("Resource '%s.%s' is not currently "
                                     "registered" % (namespace, name))

                resource.activeUsers -= 1
                self._log.debug("Released resource '%s' (%d active users)",
                                fullName, resource.activeUsers)

                # Is some one else is using the resource
                if resource.activeUsers > 0:
                    return
                self._log.debug("Resource '%s' is free, finding out if anyone "
                                "is waiting for it.", fullName)
                # Grant a request
                while True:
                    # Is there someone waiting for the resource
                    if len(resource.queue) == 0:
                        self._freeResource(resources[name])
                        del resources[name]
                        self._log.debug("No one is waiting for resource '%s', "
                                        "Clearing records.", fullName)
                        return

                    self._log.debug("Resource '%s' has %d requests in queue. "
                                    "Handling top request.", fullName,
                                    len(resource.queue))
                    nextRequest = resource.queue.pop()
                    # We lock the request to simulate a transaction. We cannot
                    # grant the request before there is a resource switch. And
                    # we can't do a resource switch before we can guarantee
                    # that the request will be granted.
                    with nextRequest.syncRoot:
                        if nextRequest.canceled():
                            self._log.debug("Request '%s' was canceled, "
                                            "Ignoring it.", nextRequest)
                            continue

                        try:
                            self._switchLockType(resource,
                                                 nextRequest.lockType)
                        except Exception:
                            self._log.warn("Resource factory failed to create "
                                           "resource '%s'. Canceling request.",
                                           fullName, exc_info=True)
                            nextRequest.cancel()
                            continue

                        nextRequest.grant()
                        contextCleanup.defer(
                            partial(nextRequest.emit,
                                    ResourceRef(namespace, name,
                                                resource.realObj,
                                                nextRequest.reqID)))

                        resource.activeUsers += 1

                        self._log.debug("Request '%s' was granted",
                                        nextRequest)
                        break

                # If the lock is exclusive were done
                if resource.currentLock == EXCLUSIVE:
                    return

                # Keep granting shared locks
                self._log.debug("This is a shared lock. Granting all shared "
                                "requests")
                while len(resource.queue) > 0:

                    nextRequest = resource.queue[-1]
                    if nextRequest.canceled():
                        resource.queue.pop()
                        continue

                    if nextRequest.lockType == EXCLUSIVE:
                        break

                    nextRequest = resource.queue.pop()
                    try:
                        nextRequest.grant()
                        contextCleanup.defer(
                            partial(nextRequest.emit,
                                    ResourceRef(namespace, name,
                                                resource.realObj,
                                                nextRequest.reqID)))
                    except RequestAlreadyProcessedError:
                        continue

                    resource.activeUsers += 1
                    self._log.debug("Request '%s' was granted (%d "
                                    "active users)", nextRequest,
                                    resource.activeUsers)


class Namespace(object):
    """
    Namespace struct
    """
    def __init__(self, factory):
        self.resources = {}
        self.lock = threading.Lock()  # rwlock.RWLock()
        self.factory = factory


class ResourceInfo(object):
    """
    Resource struct
    """
    def __init__(self, realObj, namespace, name):
        self.queue = []
        self.activeUsers = 0
        self.currentLock = None
        self.realObj = realObj
        self.namespace = namespace
        self.name = name
        self.fullName = "%s.%s" % (namespace, name)


class Owner(object):
    log = logging.getLogger('storage.ResourceManager.Owner')

    def __init__(self, ownerobject, raiseonfailure=False):
        self.ownerobject = ownerobject
        self.requests = {}
        self.resources = {}
        self.lock = threading.RLock()
        self.raiseonfailure = raiseonfailure

    def _granted(self, request, resource):
        """ internal callback used by Request
            Resource is asynchronously granted or granted after waiting
        """
        if not isinstance(request, Request):
            raise TypeError("%s is not request" % request)

        self.log.debug("%s: request granted for resource '%s'", self,
                       resource.fullName)
        self.lock.acquire()
        try:
            if request not in self.requests:
                self.log.warning("request %s not requested by %s", request,
                                 self)
                resource.release()
                return

            del self.requests[resource.fullName]

            if resource.fullName in self.resources:
                resource.release()
                raise ValueError("%s is already acquired by %s" %
                                 (request.resource, self))

            self.resources[resource.fullName] = resource
        finally:
            self.lock.release()

            if not resource.isValid:
                return

            ns = resource.namespace
            name = resource.name
            locktype = request.locktype
            if hasattr(self.ownerobject, "resourceAcquired") and ns and name:
                self.ownerobject.resourceAcquired(ns, name, locktype)

    def _canceled(self, request):
        """ internal callback used by Request.
            May be called under resource lock, so pay attention.
        """
        if not isinstance(request, Request):
            raise TypeError("%s is not request" % request)

        self.log.debug("%s: request canceled %s", self, request)
        self.lock.acquire()
        try:
            if request.fullName not in self.requests:
                self.log.warning("request %s not requested by %s", request,
                                 self)
                return

            del self.requests[request.fullName]
        finally:
            self.lock.release()

    def acquire(self, namespace, name, locktype, timeout_ms,
                raiseonfailure=None):
        fullName = "%s.%s" % (namespace, name)

        if raiseonfailure is None:
            raiseonfailure = self.raiseonfailure

        if timeout_ms is not None:
            timeout = timeout_ms / 1000.0

        self.lock.acquire()
        try:

            try:
                if fullName in self.resources:
                    raise ValueError("Owner %s: acquire: resource %s is "
                                     "already acquired" % (self, fullName))

                try:
                    resource = acquireResource(namespace, name, locktype,
                                               timeout)
                    self.resources[resource.fullName] = resource

                    if hasattr(self.ownerobject, "resourceAcquired"):
                        self.ownerobject.resourceAcquired(namespace, name,
                                                          locktype)
                except RequestTimedOutError:
                    self.log.debug("%s: request for '%s' timed out after '%f' "
                                   "seconds", self, fullName, timeout)
                    raise se.ResourceTimeout()
                except ValueError as ex:
                    self.log.debug("%s: request for '%s' could not be "
                                   "processed (%s)", self, fullName, ex)
                    raise se.InvalidResourceName()
                except KeyError:
                    self.log.debug("%s: resource '%s' does not exist", self,
                                   fullName)
                except Exception:
                    self.log.warn("Unexpected exception caught while owner "
                                  "'%s' tried to acquire '%s'", self, fullName,
                                  exc_info=True)
                    raise se.ResourceException(fullName)
            except:
                if raiseonfailure:
                    raise

                return False

            return True
        finally:
            self.lock.release()

    def _onRequestFinished(self, req, res):
        if req.granted():
            self._granted(req, res)
        elif req.canceled() and res is None:
            self._canceled(req)
        else:
            self.log.warn("%s: request '%s' returned in a weird state", self,
                          req)

    def register(self, namespace, name, locktype):
        fullName = "%s.%s" % (namespace, name)
        if fullName in self.resources:
            raise ValueError("Owner %s: acquire: resource %s is already "
                             "acquired" % (self, fullName))

        self.lock.acquire()
        try:
            if fullName in self.requests:
                raise ValueError("request %s is already requested by %s" %
                                 (fullName, self))

            try:
                request = _registerResource(namespace, name, locktype,
                                            self._onRequestFinished)
            except ValueError as ex:
                self.log.debug("%s: request for '%s' could not be processed "
                               "(%s)", self, fullName, ex)
                raise se.InvalidResourceName()
            except KeyError:
                self.log.debug("%s: resource '%s' does not exist", self,
                               fullName)
                raise se.ResourceDoesNotExist()
            except Exception:
                self.log.warn("Unexpected exception caught while owner '%s' "
                              "tried to acquire '%s'", self, fullName,
                              exc_info=True)
                raise se.ResourceException(fullName)

            if hasattr(self.ownerobject, "resourceRegistered"):
                self.ownerobject.resourceRegistered(namespace, name, locktype)

            self.requests[fullName] = request
        finally:
            self.lock.release()
        self.log.debug("%s: request registered %s", self, request)

    def cancel(self, namespace, name):
        """
        Cancel a pending request. Note that cancel may race with grant and in
        this case it is not ensured that the resource is not owned. In
        addition it is not allowed to release any resources granted due to the
        above race - the owning thread may not be aware of this!
        """
        fullName = "%s.%s" % (namespace, name)
        self.log.debug("%s: Trying to cancel request for '%s'", self, fullName)
        self.lock.acquire()
        try:
            if fullName not in self.requests:
                self.log.warning("%s: Tried to cancel resource '%s' but it was"
                                 " not requested or already canceled", self,
                                 fullName)
                return False

            request = self.requests[fullName]
            try:
                request.cancel()
                return True
            except RequestAlreadyProcessedError:
                return request.canceled()
        finally:
            self.lock.release()

    def wait(self, namespace, name, timeout_ms):
        fullName = "%s.%s" % (namespace, name)
        self.log.debug("%s: waiting for resource '%s' for %s ms", self,
                       fullName, timeout_ms)

        if timeout_ms is not None:
            timeout = timeout_ms / 1000.0

        if fullName in self.requests:
            req = self.requests[fullName]
            return req.wait(timeout)

        # req not found - check that it is not granted
        for fullName in self.resources:
            return True

        # Note that there is a risk of another thread that is racing with us
        # and releases this resource - but this should be synced above us
        raise ValueError("Owner %s: %s.%s is not requested" %
                         (self, namespace, name))

    def releaseAll(self):
        self.log.debug("Owner.releaseAll requests %s resources %s",
                       self.requests, self.resources)
        self.lock.acquire()
        try:
            self.cancelAll()

            for res in self.resources.values():
                self.release(res.namespace, res.name)
        finally:
            self.lock.release()

    def release(self, namespace, name):
        fullName = "%s.%s" % (namespace, name)
        self.lock.acquire()
        try:
            if fullName not in self.resources:
                raise ValueError("resource %s not owned by %s" %
                                 (fullName, self))

            resource = self.resources[fullName]

            if not resource.isValid:
                self.log.warn("%s: Tried to release an already released "
                              "resource '%s'", self, resource.fullName)
                return

            resource.release()

            del self.resources[resource.fullName]

        finally:
            self.lock.release()

        if hasattr(self.ownerobject, "resourceReleased"):
            self.ownerobject.resourceReleased(resource.namespace,
                                              resource.name)

    def cancelAll(self):
        self.log.debug("Owner.cancelAll requests %s", self.requests)
        self.lock.acquire()
        try:
            for req in self.requests.values():
                try:
                    req.cancel()
                except RequestAlreadyProcessedError:
                    # It must already be canceled
                    pass
        finally:
            self.lock.release()

    def ownedResources(self):
        res = self.resources.values()
        return [(r.namespace, r.name, r.getStatus()) for r in res]

    def requestedResources(self):
        reqs = self.requests.values()
        return [(r.namespace, r.name, r.locktype) for r in reqs]

    def requestsGranted(self):
        return (len(self.requests) == 0)

    @classmethod
    def validate(cls, obj):
        return isinstance(obj, cls)

    def __str__(self):
        return str(self.ownerobject)


class ResourceManagerLock(guarded.AbstractLock):
    """
    Extend AbstractLock to enable Resources to be used with guarded utilities.
    """
    def __init__(self, ns, name, mode):
        self._ns = ns
        self._name = name
        self._mode = mode

    @property
    def ns(self):
        return self._ns

    @property
    def name(self):
        return self._name

    @property
    def mode(self):
        return self._mode

    def acquire(self):
        res = acquireResource(self.ns, self.name, self.mode)
        # Locks are released by default then the last reference is garbage
        # collected.  Since we don't need the reference we'll just disable
        # autoRelease.
        res.autoRelease = False

    def release(self):
        releaseResource(self.ns, self.name)


# The single resource manager - this instance is monkeypatched by the tests.
_manager = _ResourceManager()


# Public api - client should use only these to manage resources.

def registerNamespace(namespace, factory):
    _manager.registerNamespace(namespace, factory)


def unregisterNamespace(namespace):
    _manager.unregisterNamespace(namespace)


def acquireResource(namespace, name, lockType, timeout=None):
    return _manager.acquireResource(namespace, name, lockType, timeout=timeout)


def releaseResource(namespace, name):
    _manager.releaseResource(namespace, name)


def getNamespace(*args):
    """
    Format namespace stirng from sequence of names.
    """
    return '_'.join(args)


# Private apis for the tests - clients should never use these!

def _registerResource(namespace, name, lockType, callback):
    return _manager.registerResource(namespace, name, lockType, callback)


def _getResourceStatus(namespace, name):
    return _manager.getResourceStatus(namespace, name)
