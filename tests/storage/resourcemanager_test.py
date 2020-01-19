#
# Copyright 2012 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import logging
import time
from weakref import proxy
from random import Random
import threading
from resource import getrlimit, RLIMIT_NPROC

import six
from six.moves._thread import error as ThreadError

import pytest

from vdsm.storage import exception as se
from vdsm.storage import resourceManager as rm

from storage.storagefakelib import FakeResourceManager

log = logging.getLogger("test")


class NullResourceFactory(rm.SimpleResourceFactory):
    """
    A resource factory that has no resources. Used for testing.
    """
    def resourceExists(self, name):
        return False


class ErrorResourceFactory(rm.SimpleResourceFactory):
    """
    A resource factory that has no resources. Used for testing.
    """
    def createResource(self, name, lockType):
        raise Exception("EPIC FAIL!! LOLZ!!")


class StringResourceFactory(rm.SimpleResourceFactory):
    def createResource(self, name, lockType):
        s = six.StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        def switchLockType(self, lockType):
            self.seek(0)
            name = self.read().split(":")[0]
            self.seek(0)
            self.truncate()
            self.write("%s:%s" % (name, lockType))
            self.seek(0)

        s.switchLockType = six.create_bound_method(switchLockType, s)
        return s


class SwitchFailFactory(rm.SimpleResourceFactory):
    def createResource(self, name, lockType):
        s = six.StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        def switchLockType(self, lockType):
            raise Exception("I NEVER SWITCH!!!")

        s.switchLockType = six.create_bound_method(switchLockType, s)
        return s


class CrashOnCloseFactory(rm.SimpleResourceFactory):
    def createResource(self, name, lockType):
        s = six.StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        def close(self):
            raise Exception("I NEVER CLOSE!!!")

        s.close = six.create_bound_method(close, s)
        return s


class FailAfterSwitchFactory(rm.SimpleResourceFactory):
    def __init__(self):
        self.fail = False

    def createResource(self, name, lockType):
        if self.fail:
            raise Exception("I CANT TAKE ALL THIS SWITCHING!")

        s = six.StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        factory = self

        def switchLockType(self, lockType):
            factory.fail = True
            raise Exception("FAIL!!!")

        s.switchLockType = six.create_bound_method(switchLockType, s)
        return s


@pytest.fixture
def tmp_manager(monkeypatch):
    """
    Create fresh _ResourceManager instance for testing.
    """
    manager = rm._ResourceManager()
    manager.registerNamespace("storage", rm.SimpleResourceFactory())
    manager.registerNamespace("null", NullResourceFactory())
    manager.registerNamespace("string", StringResourceFactory())
    manager.registerNamespace("error", ErrorResourceFactory())
    manager.registerNamespace("switchfail", SwitchFailFactory())
    manager.registerNamespace("crashy", CrashOnCloseFactory())
    manager.registerNamespace("failAfterSwitch", FailAfterSwitchFactory())
    monkeypatch.setattr(rm, "_manager", manager)


class OwnerObject(object):

    def __init__(self):
        self.actions = []

    def resourceAcquired(self, namespace, resource, locktype):
        self.actions.append(("acquired", namespace, resource, locktype))

    def resourceReleased(self, namespace, resource):
        self.actions.append(("released", namespace, resource))

    def getID(self):
        return "fake_id"


class TestResourceManager:

    def testErrorInFactory(self, tmp_manager):
        req = rm._registerResource(
            "error", "resource", rm.EXCLUSIVE, lambda req, res: 1)
        assert req.canceled()

    def testRegisterInvalidNamespace(self, tmp_manager):
        with pytest.raises(rm.InvalidNamespace) as e:
            rm.registerNamespace("I.HEART.DOTS", rm.SimpleResourceFactory())
        assert "I.HEART.DOTS" in str(e)

    def testFailCreateAfterSwitch(self, tmp_manager):
        resources = []

        def callback(req, res):
            resources.append(res)

        exclusive1 = rm.acquireResource(
            "failAfterSwitch", "resource", rm.EXCLUSIVE)
        sharedReq1 = rm._registerResource(
            "failAfterSwitch", "resource", rm.SHARED, callback)
        exclusive1.release()
        assert sharedReq1.canceled()
        assert resources[0] is None

    def testRegisterExistingNamespace(self, tmp_manager):
        with pytest.raises(rm.NamespaceRegistered):
            rm.registerNamespace("storage", rm.SimpleResourceFactory())

    def testRequestInvalidResource(self, tmp_manager):
        with pytest.raises(se.InvalidResourceName) as e:
            rm.acquireResource("storage", "DOT.DOT", rm.SHARED)
        assert "DOT.DOT" in str(e)

        with pytest.raises(ValueError):
            rm.acquireResource("DOT.DOT", "resource", rm.SHARED)

    def testReleaseInvalidResource(self, tmp_manager):
        with pytest.raises(ValueError):
            rm.releaseResource("DONT_EXIST", "resource")
        with pytest.raises(ValueError):
            rm.releaseResource("storage", "DOT")

    def testResourceWrapper(self, tmp_manager):
        s = six.StringIO
        with rm.acquireResource("string", "test", rm.EXCLUSIVE) as resource:
            for attr in dir(s):
                if attr == "close":
                    continue
                assert hasattr(resource, attr)

    def testAccessAttributeNotExposedByWrapper(self, tmp_manager):
        with rm.acquireResource("string", "test", rm.EXCLUSIVE) as resource:
            with pytest.raises(AttributeError):
                resource.THERE_IS_NO_WAY_I_EXIST

    def testAccessAttributeNotExposedByRequestRef(self, tmp_manager):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        req = rm._registerResource("string", "resource", rm.SHARED, callback)
        with pytest.raises(AttributeError):
            try:
                req.grant()
            finally:
                req.wait()
                resources[0].release()

    def testRequestRefStr(self, tmp_manager):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        req = rm._registerResource("string", "resource", rm.SHARED, callback)
        try:
            str(req)
        finally:
            req.wait()
            resources[0].release()

    def testRequestRefCmp(self, tmp_manager):
        resources = []
        requests = []

        def callback(req, res):
            resources.insert(0, res)
            requests.insert(0, req)

        req1 = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)
        req2 = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)

        assert req1 != req2
        assert hash(req1) != hash(req2)
        assert req1 == req1
        assert hash(req1) == hash(req1)
        assert req2 == req2
        req1.wait()
        req1Clone = requests.pop()
        assert req1 == req1Clone
        assert hash(req1) == hash(req1Clone)
        assert req1Clone != req2
        assert hash(req1Clone) != hash(req2)
        resources.pop().release()
        req2.wait()
        req2Clone = requests.pop()
        assert req2 == req2Clone
        assert hash(req2) == hash(req2Clone)
        assert req1 != req2Clone
        assert hash(req1) != hash(req2Clone)
        assert req1Clone != req2Clone
        assert hash(req1Clone) != hash(req2Clone)
        resources[0].release()

        assert req1 != "STUFF"

    def testRequestRecancel(self, tmp_manager):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        blocker = rm.acquireResource("string", "resource", rm.EXCLUSIVE)
        req = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)

        req.cancel()

        with pytest.raises(rm.RequestAlreadyProcessedError):
            req.cancel()

        blocker.release()

    def testRequestRegrant(self, tmp_manager):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        req = rm.Request("namespace", "name", rm.EXCLUSIVE, callback)
        req.grant()
        with pytest.raises(rm.RequestAlreadyProcessedError):
            req.grant()

    def testRequestWithBadCallbackOnCancel(self, tmp_manager):
        def callback(req, res):
            raise Exception("BUY MILK!")

        blocker = rm.acquireResource("string", "resource", rm.EXCLUSIVE)
        req = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)

        req.cancel()

        blocker.release()

    def testRequestWithBadCallbackOnGrant(self, tmp_manager):
        def callback(req, res):
            res.release()
            raise Exception("BUY MILK!")

        req = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)
        req.wait()

    def testRereleaseResource(self, tmp_manager):
        res = rm.acquireResource("string", "resource", rm.EXCLUSIVE)
        res.release()
        res.release()

    def testCancelExclusiveBetweenShared(self, tmp_manager):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        exclusive1 = rm.acquireResource("string", "resource", rm.EXCLUSIVE)
        sharedReq1 = rm._registerResource(
            "string", "resource", rm.SHARED, callback)
        sharedReq2 = rm._registerResource(
            "string", "resource", rm.SHARED, callback)
        exclusiveReq1 = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)
        sharedReq3 = rm._registerResource(
            "string", "resource", rm.SHARED, callback)
        sharedReq4 = rm._registerResource(
            "string", "resource", rm.SHARED, callback)

        assert not sharedReq1.granted()
        assert not sharedReq2.granted()
        assert not exclusiveReq1.granted()
        assert not sharedReq3.granted()
        assert not sharedReq4.granted()

        exclusiveReq1.cancel()
        resources.pop()

        assert not sharedReq1.granted()
        assert not sharedReq2.granted()
        assert not exclusiveReq1.granted()
        assert exclusiveReq1.canceled()
        assert not sharedReq3.granted()
        assert not sharedReq4.granted()

        exclusive1.release()
        assert sharedReq1.granted()
        assert sharedReq2.granted()
        assert sharedReq3.granted()
        assert sharedReq4.granted()

        while len(resources) > 0:
            resources.pop().release()

    @pytest.mark.parametrize("namespace", ["string", "crashy", "switchfail"])
    def testResourceLockSwitch(self, namespace, tmp_manager):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        exclusive1 = rm.acquireResource(namespace, "resource", rm.EXCLUSIVE)
        sharedReq1 = rm._registerResource(
            namespace, "resource", rm.SHARED, callback)
        sharedReq2 = rm._registerResource(
            namespace, "resource", rm.SHARED, callback)
        exclusive2 = rm._registerResource(
            namespace, "resource", rm.EXCLUSIVE, callback)
        exclusive3 = rm._registerResource(
            namespace, "resource", rm.EXCLUSIVE, callback)
        sharedReq3 = rm._registerResource(
            namespace, "resource", rm.SHARED, callback)

        assert exclusive1.read() == "resource:exclusive"
        exclusive1.release()
        assert resources[-1].read() == "resource:shared"
        resources.pop().release()
        assert resources[-1].read() == ""
        resources.pop().release()
        assert resources[-1].read() == "resource:exclusive"
        resources.pop().release()
        assert resources[-1].read() == ""
        resources.pop().release()
        assert resources[-1].read() == "resource:shared"
        resources.pop().release()

        # Silense flake8 unused local variables warnings.
        sharedReq1
        sharedReq2
        exclusive2
        exclusive3
        sharedReq3

    def testResourceAcquireTimeout(self, tmp_manager):
        exclusive1 = rm.acquireResource("string", "resource", rm.EXCLUSIVE)
        with pytest.raises(rm.RequestTimedOutError):
            rm.acquireResource("string", "resource", rm.EXCLUSIVE, 1)
        exclusive1.release()

    def testResourceAcquireInvalidTimeout(self, tmp_manager):
        with pytest.raises(TypeError):
            rm.acquireResource("string", "resource", rm.EXCLUSIVE, "A")

    def testResourceInvalidation(self, tmp_manager):
        resource = rm.acquireResource("string", "test", rm.EXCLUSIVE)
        resource.write("dsada")
        resource.release()
        with pytest.raises(Exception):
            resource.write("test")

    def testResourceAutorelease(self, tmp_manager):
        log.info("Acquiring resource", extra={'resource': "bob"})
        res = rm.acquireResource("storage", "resource", rm.SHARED)
        resProxy = proxy(res)
        res = None
        # wait for object to die
        log.info("Waiting for request")
        try:
            while True:
                resProxy.granted()
        except:
            pass
        log.info("Waiting for autoclean")
        while True:
            resStatus = rm._getResourceStatus("storage", "resource")
            if resStatus == rm.LockState.free:
                break
            time.sleep(1)

    def testAcquireResourceShared(self, tmp_manager):
        res1 = rm.acquireResource("storage", "resource", rm.SHARED)
        res2 = rm.acquireResource("storage", "resource", rm.SHARED, 10)

        res1.release()
        res2.release()

    def testResourceStatuses(self, tmp_manager):
        status = rm._getResourceStatus("storage", "resource")
        assert status == rm.LockState.free
        exclusive1 = rm.acquireResource("storage", "resource", rm.EXCLUSIVE)
        status = rm._getResourceStatus("storage", "resource")
        assert status == rm.LockState.locked
        exclusive1.release()
        shared1 = rm.acquireResource("storage", "resource", rm.SHARED)
        status = rm._getResourceStatus("storage", "resource")
        assert status == rm.LockState.shared
        shared1.release()
        with pytest.raises(KeyError):
            status = rm._getResourceStatus("null", "resource")

    def testAcquireNonExistingResource(self, tmp_manager):
        with pytest.raises(KeyError):
            rm.acquireResource("null", "resource", rm.EXCLUSIVE)

    def testAcquireInvalidLockType(self, tmp_manager):
        with pytest.raises(rm.InvalidLockType) as e:
            rm.acquireResource("storage", "resource", "invalid_locktype")
        assert "invalid_locktype" in str(e)

    def testAcquireResourceExclusive(self, tmp_manager):
        resources = []

        def callback(req, res):
            resources.append(res)

        exclusive1 = rm.acquireResource("storage", "resource", rm.EXCLUSIVE)
        sharedReq1 = rm._registerResource(
            "storage", "resource", rm.SHARED, callback)
        sharedReq2 = rm._registerResource(
            "storage", "resource", rm.SHARED, callback)
        exclusiveReq1 = rm._registerResource(
            "storage", "resource", rm.EXCLUSIVE, callback)
        exclusiveReq2 = rm._registerResource(
            "storage", "resource", rm.EXCLUSIVE, callback)

        assert not sharedReq1.granted()
        assert not sharedReq2.granted()
        assert not exclusiveReq1.granted()
        assert not exclusiveReq2.granted()
        exclusive1.release()

        assert sharedReq1.granted()
        assert sharedReq2.granted()
        assert not exclusiveReq1.granted()
        assert not exclusiveReq2.granted()
        resources.pop().release()  # Shared 1

        assert not exclusiveReq1.granted()
        assert not exclusiveReq2.granted()
        resources.pop().release()  # Shared 2

        assert exclusiveReq1.granted()
        assert not exclusiveReq2.granted()
        resources.pop().release()  # exclusiveReq 1

        assert exclusiveReq2.granted()
        resources.pop().release()  # exclusiveReq 2

    def testCancelRequest(self, tmp_manager):
        resources = []

        def callback(req, res):
            resources.append(res)

        exclusiveReq1 = rm._registerResource(
            "storage", "resource", rm.EXCLUSIVE, callback)
        exclusiveReq2 = rm._registerResource(
            "storage", "resource", rm.EXCLUSIVE, callback)
        exclusiveReq3 = rm._registerResource(
            "storage", "resource", rm.EXCLUSIVE, callback)

        assert exclusiveReq1.granted()
        assert not exclusiveReq2.canceled()
        assert not exclusiveReq3.granted()

        exclusiveReq2.cancel()
        assert exclusiveReq2.canceled()
        assert resources.pop() is None  # exclusiveReq 2

        resources.pop().release()  # exclusiveReq 1

        assert exclusiveReq3.granted()
        resources.pop().release()  # exclusiveReq 3

    @pytest.mark.slow
    @pytest.mark.stress
    def testStressTest(self, tmp_manager):
        """
        This tests raises thousands of threads and tries to acquire the same
        resource.
        """
        queue = []

        procLimit, _ = getrlimit(RLIMIT_NPROC)
        procLimit *= 0.5
        procLimit = int(procLimit)
        procLimit = min(procLimit, 4096)
        threadLimit = threading.Semaphore(procLimit)
        maxedOut = False

        def callback(req, res):
            queue.insert(0, (req, res))

        def register():
            time.sleep(rnd.randint(0, 4))
            rm._registerResource("string",
                                 "resource",
                                 lockTranslator[rnd.randint(0, 1)],
                                 callback)
            threadLimit.release()

        def releaseShared(req, res):
            assert req.lockType == rm.SHARED
            res.release()
            threadLimit.release()

        def releaseUnknown(req, res):
            res.release()
            threadLimit.release()

        rnd = Random()

        lockTranslator = [rm.EXCLUSIVE, rm.SHARED]

        threads = []
        for i in range(procLimit // 2):
            t = threading.Thread(target=register)
            try:
                t.start()
            except ThreadError:
                # Reached thread limit, bail out
                # Mark test as "maxedOut" which will be used later to make sure
                # we clean up without using threads.
                maxedOut = True
                break

            threadLimit.acquire()
            threads.append(t)

        n = 0
        releaseThreads = []
        while n < len(threads):
            queueLen = len(queue)
            # If there is more than 1 item in the queue we know it's a shared
            # lock so we should check for sanity. If there is one item it can
            # be either.
            if queueLen == 1:
                f = releaseUnknown
            else:
                f = releaseShared

            for i in range(queueLen):
                if maxedOut:
                    f(*queue.pop())
                else:
                    threadLimit.acquire()
                    t = threading.Thread(target=f, args=queue.pop())
                    try:
                        t.start()
                    except ThreadError:
                        threadLimit.release()
                        f(*queue.pop())
                    else:
                        releaseThreads.append(t)

                n += 1

        for t in releaseThreads:
            t.join()


class TestResourceManagerLock:

    def test_properties(self):
        a = rm.ResourceManagerLock('ns', 'name', 'mode')
        assert a.ns == 'ns'
        assert a.name == 'name'
        assert a.mode == 'mode'

    @pytest.mark.parametrize('a, b', [
        (('nsA', 'nameA', 'mode'), ('nsB', 'nameA', 'mode')),
        (('nsA', 'nameA', 'mode'), ('nsA', 'nameB', 'mode')),
    ])
    def test_less_than(self, a, b):
        b = rm.ResourceManagerLock(*b)
        a = rm.ResourceManagerLock(*a)
        assert a < b

    def test_equality(self):
        a = rm.ResourceManagerLock('ns', 'name', 'mode')
        b = rm.ResourceManagerLock('ns', 'name', 'mode')
        assert a == b

    def test_mode_used_for_equality(self):
        a = rm.ResourceManagerLock('nsA', 'nameA', 'modeA')
        b = rm.ResourceManagerLock('nsA', 'nameA', 'modeB')
        assert a != b

    def test_mode_ignored_for_sorting(self):
        a = rm.ResourceManagerLock('nsA', 'nameA', 'modeA')
        b = rm.ResourceManagerLock('nsA', 'nameA', 'modeB')
        assert not a < b
        assert not b < a

    def test_acquire_release(self, monkeypatch):
        monkeypatch.setattr(rm, "_manager", FakeResourceManager())
        lock = rm.ResourceManagerLock('ns_A', 'name_A', rm.SHARED)
        expected = []
        lock.acquire()
        expected.append(('acquireResource',
                         (lock.ns, lock.name, lock.mode),
                         {"timeout": None}))
        assert expected == rm._manager.__calls__
        lock.release()
        expected.append(('releaseResource', (lock.ns, lock.name), {}))
        assert expected == rm._manager.__calls__

    def test_repr(self):
        mode = rm.SHARED
        lock = rm.ResourceManagerLock('ns', 'name', mode)
        lock_string = str(lock)
        assert "ResourceManagerLock" in lock_string
        assert "ns=ns" in lock_string
        assert "name=name" in lock_string
        assert "mode=" + mode in lock_string
        assert "%x" % id(lock) in lock_string


class TestResourceOwner:

    def test_acquire_release_resource(self, tmp_manager):
        resources = [
            ("storage", "A", rm.SHARED),
            ("storage", "B", rm.SHARED),
            ("storage", "C", rm.EXCLUSIVE),
        ]
        actions = [
            ("acquired", "storage", "A", rm.SHARED),
            ("acquired", "storage", "B", rm.SHARED),
            ("acquired", "storage", "C", rm.EXCLUSIVE),
            ("released", "storage", "A"),
            ("released", "storage", "B"),
            ("released", "storage", "C"),
        ]
        owner_object = OwnerObject()
        owner = rm.Owner(owner_object, raiseonfailure=True)

        for namespace, resources, locktype in resources:
            owner.acquire(namespace, resources, locktype, timeout_ms=5000)
        owner.releaseAll()

        assert owner_object.actions == actions

    def test_release_empty_resources(self, tmp_manager):
        owner_object = OwnerObject()
        owner = rm.Owner(owner_object, raiseonfailure=True)
        owner.releaseAll()
        assert owner_object.actions == []

    @pytest.mark.parametrize('old_locktype, new_locktype', [
        pytest.param(rm.SHARED, rm.SHARED,
                     id="double acquire for shared lock"),
        pytest.param(rm.EXCLUSIVE, rm.EXCLUSIVE,
                     id="double acquire for exclusive lock"),
        pytest.param(rm.SHARED, rm.EXCLUSIVE,
                     id="switch from shared to exclusive lock"),
        pytest.param(rm.EXCLUSIVE, rm.SHARED,
                     id="switch from exclusive to shared lock"),
    ])
    def test_acquire_twice(self, old_locktype, new_locktype, tmp_manager):
        owner_object = OwnerObject()
        owner = rm.Owner(owner_object, raiseonfailure=True)
        # Acquire a resource within time period allowing it to happen
        owner.acquire("storage", "resource", old_locktype, timeout_ms=5000)

        # Requiring an already acquired resource should fail immediately
        with pytest.raises(rm.ResourceAlreadyAcquired) as e:
            owner.acquire("storage", "resource", new_locktype, timeout_ms=1)
        owner.releaseAll()

        error_str = str(e)
        assert "storage" in error_str
        assert "resource" in error_str
        assert "fake_id" in error_str

    @pytest.mark.parametrize('locktype', [
        rm.SHARED,
        rm.EXCLUSIVE,
    ])
    def test_acquire_missing_resource(self, locktype, tmp_manager):
        owner_object = OwnerObject()
        owner = rm.Owner(owner_object, raiseonfailure=True)
        # The null resource factory always determines that the requested
        # resource does not exist, hence ResourceDoesNotExist is expected.
        with pytest.raises(rm.ResourceDoesNotExist) as e:
            owner.acquire("null", "no_such_resource", locktype, timeout_ms=1)
        error_str = str(e)
        assert "no_such_resource" in error_str
        assert "null" in error_str

    @pytest.mark.parametrize('locktype', [
        rm.SHARED,
        rm.EXCLUSIVE,
    ])
    def test_acquire_error(self, locktype, tmp_manager):
        owner_object = OwnerObject()
        owner = rm.Owner(owner_object, raiseonfailure=True)
        # The error resource factory is expected to raise a ResourceException
        # upon creation of any resource.
        with pytest.raises(se.ResourceException):
            owner.acquire("error", "any", locktype, timeout_ms=1)
        owner.releaseAll()

    @pytest.mark.parametrize('locktype', ["invalid_lock_type", 7, -1])
    def test_acquire_invalid_locktype(self, locktype, tmp_manager):
        owner_object = OwnerObject()
        owner = rm.Owner(owner_object, raiseonfailure=True)
        with pytest.raises(se.ResourceException):
            owner.acquire("storage", "resource", locktype, timeout_ms=1)
        assert owner_object.actions == []
