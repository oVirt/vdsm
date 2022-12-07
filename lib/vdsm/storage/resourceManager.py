# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import threading
import logging
import re
import weakref
from functools import partial
from uuid import uuid4

from six.moves import queue

from vdsm import utils
from vdsm.common import concurrent
from vdsm.common.logutils import SimpleLogAdapter
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import rwlock

log = logging.getLogger("storage.resourcemanager")


# Errors

class ResourceManagerError(Exception):
    pass


class NamespaceRegistered(ResourceManagerError):
    """ Raised if a namespace is already registered """


class RequestAlreadyProcessedError(ResourceManagerError):
    pass


class RequestTimedOutError(ResourceManagerError):
    pass


class ResourceAlreadyAcquired(ResourceManagerError):
    pass


class InvalidLockType(ResourceManagerError):
    pass


class InvalidNamespace(ResourceManagerError):
    pass


class ResourceDoesNotExist(ResourceManagerError):
    pass

# TODO : Consider changing when we decided on a unified way of representing
#        enums.


# Lock types.
SHARED = "shared"
EXCLUSIVE = "exclusive"

# Lock statuses.
STATUS_FREE = "free"
STATUS_SHARED = "shared"
STATUS_LOCKED = "locked"


def _statusFromType(locktype):
    if str(locktype) == SHARED:
        return STATUS_SHARED
    if str(locktype) == EXCLUSIVE:
        return STATUS_LOCKED
    raise InvalidLockType("Invalid locktype %r was used" % locktype)


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
                                 "full_name", "lockType", "status", "granted",
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

        return self._realRequset == other._realRequset

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self._realRequset)


class Request(object):
    """
    Internal request object, don't use directly
    """
    namespace = property(lambda self: self._namespace)
    name = property(lambda self: self._name)
    full_name = property(lambda self: "%s.%s" % (self._namespace, self._name))
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
        self._log = SimpleLogAdapter(
            log, {"ResName": self.full_name, "ReqID": self.reqID})

    def cancel(self):
        with self._syncRoot:
            if not self._isActive:
                self._log.warning("Tried to cancel a processed request")
                raise RequestAlreadyProcessedError("Cannot cancel a processed "
                                                   "request")

            self._isActive = False
            self._isCanceled = True

            self._log.debug("Canceled request")
            try:
                self._callback(RequestRef(self), None)
            except Exception:
                self._log.warning("Request callback threw an exception",
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
                self._log.warning("Tried to grant a processed request")
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
            self._log.warning("Request callback threw an exception",
                              exc_info=True)

    def wait(self, timeout=None):
        return self._doneEvent.wait(timeout)

    def granted(self):
        with self._syncRoot:
            return (not self._isCanceled) and self._doneEvent.isSet()

    def __str__(self):
        return "Request for %s - %s: %s" % (self.full_name, self.lockType,
                                            self._status())


class ResourceRef(object):
    """
    A reference to a resource. Can be used to conveniently modify an owned
    resource.

    This object will auto release the referenced resource unless autorelease
    is set to `False`
    """
    namespace = property(lambda self: self._namespace)
    name = property(lambda self: self._name)
    full_name = property(lambda self: "%s.%s" % (self._namespace, self._name))

    # States whether this reference is pointing to an owned reference
    isValid = property(lambda self: self._isValid)

    def __init__(self, namespace, name, wrappedObject=None,
                 resRefID=str(uuid4())):
        self._namespace = namespace
        self._name = name
        self._log = SimpleLogAdapter(
            log, {"ResName": self.full_name, "ResRefID": resRefID})

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
                self._log.warning("Tried to re-release a resource. Request "
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
                log.warning("Resource reference was not properly released. "
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
            self.full_name, self.isValid,
            repr(self.__wrappedObject) if self.isValid else None)


class _ResourceManager(object):
    """
    Manages all the resources in the application.

    This class is for internal usage only, clients should use the module
    interface.
    """
    _namespaceValidator = re.compile(r"^[\w\d_-]+$")
    _resourceNameValidator = re.compile(r"^[^\s.]+$")

    def __init__(self):
        self._syncRoot = rwlock.RWLock()
        self._namespaces = {}

    def registerNamespace(self, namespace, factory):
        if not self._namespaceValidator.match(namespace):
            raise InvalidNamespace(f"Invalid namespace name {namespace!r}")

        if namespace in self._namespaces:
            raise NamespaceRegistered(
                f"Namespace '{namespace}' already registered")

        with self._syncRoot.exclusive:
            if namespace in self._namespaces:
                raise NamespaceRegistered(
                    f"Namespace '{namespace}' already registered")

            log.debug("Registering namespace '%s'", namespace)

            self._namespaces[namespace] = Namespace(factory)

    def getResourceStatus(self, namespace, name):
        if not self._resourceNameValidator.match(name):
            raise se.InvalidResourceName(name)

        with self._syncRoot.shared:
            try:
                namespaceObj = self._namespaces[namespace]
            except KeyError:
                raise ValueError(
                    f"Namespace '{namespace}' is not registered "
                    "with this manager")
            resources = namespaceObj.resources
            with namespaceObj.lock:
                if not namespaceObj.factory.resourceExists(name):
                    raise KeyError(f"No such resource '{namespace}.{name}'")

                if name not in resources:
                    return STATUS_FREE

                return _statusFromType(resources[name].currentLock)

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
                    log.warning(
                        "Lock type switch failed on resource '%s'. "
                        "Falling back to object recreation.",
                        resourceInfo.full_name,
                        exc_info=True)

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
                log.warning("Couldn't close resource '%s'.",
                            resourceInfo.full_name, exc_info=True)

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
        full_name = "%s.%s" % (namespace, name)

        if not self._resourceNameValidator.match(name):
            raise se.InvalidResourceName(name)

        if lockType not in (SHARED, EXCLUSIVE):
            raise InvalidLockType("Invalid locktype %r was used" % lockType)

        request = Request(namespace, name, lockType, callback)
        log.debug("Trying to register resource '%s' for lock type '%s'",
                  full_name, lockType)
        with utils.RollbackContext() as contextCleanup, self._syncRoot.shared:
            try:
                namespaceObj = self._namespaces[namespace]
            except KeyError:
                raise ValueError(
                    f"Namespace '{namespace}' is not registered "
                    "with this manager")

            resources = namespaceObj.resources
            with namespaceObj.lock:
                try:
                    resource = resources[name]
                except KeyError:
                    if not namespaceObj.factory.resourceExists(name):
                        raise KeyError(f"No such resource '{full_name}'")
                else:
                    if len(resource.queue) == 0 and \
                            resource.currentLock == SHARED and \
                            request.lockType == SHARED:
                        resource.activeUsers += 1
                        log.debug("Resource '%s' found in shared state "
                                  "and queue is empty, Joining current "
                                  "shared lock (%d active users)",
                                  full_name, resource.activeUsers)
                        request.grant()
                        contextCleanup.defer(request.emit,
                                             ResourceRef(namespace, name,
                                                         resource.realObj,
                                                         request.reqID))
                        return RequestRef(request)

                    resource.queue.insert(0, request)
                    log.debug("Resource '%s' is currently locked, "
                              "Entering queue (%d in queue)",
                              full_name, len(resource.queue))
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
                    log.warning(
                        "Resource factory failed to create resource"
                        " '%s'. Canceling request.", full_name, exc_info=True)
                    contextCleanup.defer(request.cancel)
                    return RequestRef(request)

                resource = resources[name] = ResourceInfo(obj, namespace, name)
                resource.currentLock = request.lockType
                resource.activeUsers += 1

                log.debug("Resource '%s' is free. Now locking as '%s' "
                          "(1 active user)",
                          full_name, request.lockType)
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
        full_name = "%s.%s" % (namespace, name)

        log.debug("Trying to release resource '%s'", full_name)
        with utils.RollbackContext() as contextCleanup, self._syncRoot.shared:
            try:
                namespaceObj = self._namespaces[namespace]
            except KeyError:
                raise ValueError(
                    f"Namespace '{namespace}' is not registered "
                    "with this manager")
            resources = namespaceObj.resources

            with namespaceObj.lock:
                try:
                    resource = resources[name]
                except KeyError:
                    raise ValueError(
                        f"Resource '{namespace}.{name}' is not "
                        "currently registered")

                resource.activeUsers -= 1
                log.debug("Released resource '%s' (%d active users)",
                          full_name, resource.activeUsers)

                # Is some one else is using the resource
                if resource.activeUsers > 0:
                    return
                log.debug("Resource '%s' is free, finding out if anyone "
                          "is waiting for it.", full_name)
                # Grant a request
                while True:
                    # Is there someone waiting for the resource
                    if len(resource.queue) == 0:
                        self._freeResource(resources[name])
                        del resources[name]
                        log.debug("No one is waiting for resource '%s', "
                                  "Clearing records.", full_name)
                        return

                    log.debug("Resource '%s' has %d requests in queue. "
                              "Handling top request.",
                              full_name, len(resource.queue))
                    nextRequest = resource.queue.pop()
                    # We lock the request to simulate a transaction. We cannot
                    # grant the request before there is a resource switch. And
                    # we can't do a resource switch before we can guarantee
                    # that the request will be granted.
                    with nextRequest.syncRoot:
                        if nextRequest.canceled():
                            log.debug("Request '%s' was canceled, Ignoring it",
                                      nextRequest)
                            continue

                        try:
                            self._switchLockType(resource,
                                                 nextRequest.lockType)
                        except Exception:
                            log.warning(
                                "Resource factory failed to create "
                                "resource '%s'. Canceling request.",
                                full_name, exc_info=True)
                            nextRequest.cancel()
                            continue

                        nextRequest.grant()
                        contextCleanup.defer(
                            partial(nextRequest.emit,
                                    ResourceRef(namespace, name,
                                                resource.realObj,
                                                nextRequest.reqID)))

                        resource.activeUsers += 1

                        log.debug("Request '%s' was granted", nextRequest)
                        break

                # If the lock is exclusive were done
                if resource.currentLock == EXCLUSIVE:
                    return

                # Keep granting shared locks
                log.debug("This is a shared lock. Granting shared requests")
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
                    log.debug("Request '%s' was granted (%d active users)",
                              nextRequest, resource.activeUsers)


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
        self.full_name = "%s.%s" % (namespace, name)


class Owner(object):

    def __init__(self, ownerobject, raiseonfailure=False):
        self.ownerobject = ownerobject
        self.resources = {}
        self.lock = threading.RLock()
        self.raiseonfailure = raiseonfailure

    def acquire(self, namespace, name, locktype, timeout_ms,
                raiseonfailure=None):
        full_name = "%s.%s" % (namespace, name)

        if raiseonfailure is None:
            raiseonfailure = self.raiseonfailure

        if timeout_ms is not None:
            timeout = timeout_ms / 1000.0

        with self.lock:
            try:
                if full_name in self.resources:
                    raise ResourceAlreadyAcquired(
                        f"{full_name} is already acquired "
                        f"by {self.ownerobject.getID()}")
                try:
                    resource = acquireResource(namespace, name, locktype,
                                               timeout)
                    self.resources[resource.full_name] = resource

                    if hasattr(self.ownerobject, "resourceAcquired"):
                        self.ownerobject.resourceAcquired(namespace, name,
                                                          locktype)
                except RequestTimedOutError:
                    log.debug(
                        "%s: request for '%s' timed out after '%f' seconds",
                        self, full_name, timeout)
                    raise se.ResourceTimeout()
                except ValueError as ex:
                    log.debug(
                        "%s: request for '%s' could not be processed (%s)",
                        self, full_name, ex)
                    raise se.InvalidResourceName(name)
                except KeyError:
                    log.debug(
                        "%s: resource '%s' does not exist",
                        self, full_name)
                    raise ResourceDoesNotExist(
                        f"Resource {full_name} does not exist")
                except Exception:
                    log.warning(
                        "Unexpected exception caught while owner '%s' tried "
                        "to acquire '%s'",
                        self, full_name, exc_info=True)
                    raise se.ResourceException(full_name)
            except:
                if raiseonfailure:
                    raise

                return False

            return True

    def releaseAll(self):
        log.debug("Owner.releaseAll resources %s", self.resources)
        with self.lock:
            for res in list(self.resources.values()):
                self._release(res.namespace, res.name)

    def _release(self, namespace, name):
        full_name = "%s.%s" % (namespace, name)
        with self.lock:
            if full_name not in self.resources:
                raise ValueError(f"resource {full_name} not owned by {self}")

            resource = self.resources[full_name]

            if not resource.isValid:
                log.warning(
                    "%s: Tried to release an already released resource "
                    "'%s'",
                    self, resource.full_name)
                return

            resource.release()

            del self.resources[resource.full_name]

        if hasattr(self.ownerobject, "resourceReleased"):
            self.ownerobject.resourceReleased(resource.namespace,
                                              resource.name)

    @classmethod
    def validate(cls, obj):
        return isinstance(obj, cls)

    def __str__(self):
        return str(self.ownerobject)


class Lock(guarded.AbstractLock):
    """
    Extend AbstractLock to enable Resources to be used with guarded utilities.

    This lock can also be used as a context manager.
    """
    def __init__(self, ns, name, mode):
        self._ns = ns
        self._name = name
        self._mode = mode

    # guarded.AbstractLock interface.

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

    # Contextmanger interface.

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, t, v, tb):
        try:
            self.release()
        except Exception as e:
            if t is None:
                raise

            # Log the release error without the user error which is propagated
            # to the caller. This avoids the annoying "while handling this
            # error, another error occurred" double traceback.
            e.__cause__ = None
            log.exception("Error releasing resource manager lock")


# The single resource manager - this instance is monkeypatched by the tests.
_manager = _ResourceManager()


# Public api - client should use only these to manage resources.

def registerNamespace(namespace, factory):
    _manager.registerNamespace(namespace, factory)


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
