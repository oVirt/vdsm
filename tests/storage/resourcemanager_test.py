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
import time
from weakref import proxy
from random import Random
import threading
from six.moves._thread import error as ThreadError
from StringIO import StringIO
import types
from resource import getrlimit, RLIMIT_NPROC

import pytest

from vdsm.storage import resourceManager as rm

from monkeypatch import MonkeyPatch
from storage.storagefakelib import FakeResourceManager
from testlib import expandPermutations, permutations
from testlib import VdsmTestCase


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
        s = StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        def switchLockType(self, lockType):
            self.seek(0)
            name = self.read().split(":")[0]
            self.seek(0)
            self.truncate()
            self.write("%s:%s" % (name, lockType))
            self.seek(0)

        s.switchLockType = types.MethodType(switchLockType, s, StringIO)
        return s


class SwitchFailFactory(rm.SimpleResourceFactory):
    def createResource(self, name, lockType):
        s = StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        def switchLockType(self, lockType):
            raise Exception("I NEVER SWITCH!!!")

        s.switchLockType = types.MethodType(switchLockType, s, StringIO)
        return s


class CrashOnCloseFactory(rm.SimpleResourceFactory):
    def createResource(self, name, lockType):
        s = StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        def close(self):
            raise Exception("I NEVER CLOSE!!!")

        s.close = types.MethodType(close, s, StringIO)
        return s


class FailAfterSwitchFactory(rm.SimpleResourceFactory):
    def __init__(self):
        self.fail = False

    def createResource(self, name, lockType):
        if self.fail:
            raise Exception("I CANT TAKE ALL THIS SWITCHING!")

        s = StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        factory = self

        def switchLockType(self, lockType):
            factory.fail = True
            raise Exception("FAIL!!!")

        s.switchLockType = types.MethodType(switchLockType, s, StringIO)
        return s


def manager():
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
    return manager


class TestResourceManager(VdsmTestCase):

    @MonkeyPatch(rm, "_manager", manager())
    def testErrorInFactory(self):
        req = rm._registerResource(
            "error", "resource", rm.EXCLUSIVE, lambda req, res: 1)
        self.assertTrue(req.canceled())

    @MonkeyPatch(rm, "_manager", manager())
    def testRegisterInvalidNamespace(self):
        try:
            rm.registerNamespace("I.HEART.DOTS", rm.SimpleResourceFactory())
        except ValueError:
            return

        self.fail("Managed to register an invalid namespace")

    @MonkeyPatch(rm, "_manager", manager())
    def testFailCreateAfterSwitch(self):
        resources = []

        def callback(req, res):
            resources.append(res)

        exclusive1 = rm.acquireResource(
            "failAfterSwitch", "resource", rm.EXCLUSIVE)
        sharedReq1 = rm._registerResource(
            "failAfterSwitch", "resource", rm.SHARED, callback)
        exclusive1.release()
        self.assertTrue(sharedReq1.canceled())
        self.assertEqual(resources[0], None)

    @MonkeyPatch(rm, "_manager", manager())
    def testRegisterExistingNamespace(self):
        self.assertRaises(rm.NamespaceRegistered, rm.registerNamespace,
                          "storage", rm.SimpleResourceFactory())

    @MonkeyPatch(rm, "_manager", manager())
    def testResourceSwitchLockTypeFail(self):
        self.testResourceLockSwitch("switchfail")

    @MonkeyPatch(rm, "_manager", manager())
    def testRequestInvalidResource(self):
        self.assertRaises(ValueError, rm.acquireResource,
                          "storage", "DOT.DOT", rm.SHARED)
        self.assertRaises(ValueError, rm.acquireResource,
                          "DOT.DOT", "resource", rm.SHARED)

    @MonkeyPatch(rm, "_manager", manager())
    def testReleaseInvalidResource(self):
        self.assertRaises(ValueError, rm.releaseResource,
                          "DONT_EXIST", "resource")
        self.assertRaises(ValueError, rm.releaseResource, "storage",
                          "DOT")

    @MonkeyPatch(rm, "_manager", manager())
    def testResourceWrapper(self):
        s = StringIO
        with rm.acquireResource("string", "test", rm.EXCLUSIVE) as resource:
            for attr in dir(s):
                if attr == "close":
                    continue
                self.assertTrue(hasattr(resource, attr))

    @MonkeyPatch(rm, "_manager", manager())
    def testAccessAttributeNotExposedByWrapper(self):
        with rm.acquireResource("string", "test", rm.EXCLUSIVE) as resource:
            try:
                resource.THERE_IS_NO_WAY_I_EXIST
            except AttributeError:
                return
            except Exception as ex:
                self.fail("Wrong exception was raised. "
                          "Expected AttributeError got %s",
                          ex.__class__.__name__)

        self.fail("Managed to access an attribute not exposed by wrapper")

    @MonkeyPatch(rm, "_manager", manager())
    def testAccessAttributeNotExposedByRequestRef(self):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        req = rm._registerResource("string", "resource", rm.SHARED, callback)
        try:
            req.grant()
        except AttributeError:
            return
        except Exception as ex:
            self.fail("Wrong exception was raised. "
                      "Expected AttributeError got %s", ex.__class__.__name__)
        finally:
            req.wait()
            resources[0].release()

        self.fail("Managed to access an attribute not exposed by wrapper")

    @MonkeyPatch(rm, "_manager", manager())
    def testRequestRefStr(self):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        req = rm._registerResource("string", "resource", rm.SHARED, callback)
        try:
            str(req)
        finally:
            req.wait()
            resources[0].release()

    @MonkeyPatch(rm, "_manager", manager())
    def testRequestRefCmp(self):
        resources = []
        requests = []

        def callback(req, res):
            resources.insert(0, res)
            requests.insert(0, req)

        req1 = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)
        req2 = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)

        self.assertNotEqual(req1, req2)
        self.assertEqual(req1, req1)
        self.assertEqual(req2, req2)
        req1.wait()
        req1Clone = requests.pop()
        self.assertEqual(req1, req1Clone)
        self.assertNotEqual(req1Clone, req2)
        resources.pop().release()
        req2.wait()
        req2Clone = requests.pop()
        self.assertEqual(req2, req2Clone)
        self.assertNotEqual(req1, req2Clone)
        self.assertNotEqual(req1Clone, req2Clone)
        resources[0].release()

        self.assertNotEqual(req1, "STUFF")

    @MonkeyPatch(rm, "_manager", manager())
    def testRequestRecancel(self):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        blocker = rm.acquireResource("string", "resource", rm.EXCLUSIVE)
        req = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)

        req.cancel()

        self.assertRaises(rm.RequestAlreadyProcessedError, req.cancel)

        blocker.release()

    @MonkeyPatch(rm, "_manager", manager())
    def testRequestRegrant(self):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        req = rm.Request("namespace", "name", rm.EXCLUSIVE, callback)
        req.grant()
        self.assertRaises(rm.RequestAlreadyProcessedError, req.grant)

    @MonkeyPatch(rm, "_manager", manager())
    def testRequestWithBadCallbackOnCancel(self):
        def callback(req, res):
            raise Exception("BUY MILK!")

        blocker = rm.acquireResource("string", "resource", rm.EXCLUSIVE)
        req = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)

        req.cancel()

        blocker.release()

    @MonkeyPatch(rm, "_manager", manager())
    def testRequestWithBadCallbackOnGrant(self):
        def callback(req, res):
            res.release()
            raise Exception("BUY MILK!")

        req = rm._registerResource(
            "string", "resource", rm.EXCLUSIVE, callback)
        req.wait()

    @MonkeyPatch(rm, "_manager", manager())
    def testRereleaseResource(self):
        res = rm.acquireResource("string", "resource", rm.EXCLUSIVE)
        res.release()
        res.release()

    @MonkeyPatch(rm, "_manager", manager())
    def testCancelExclusiveBetweenShared(self):
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

        self.assertFalse(sharedReq1.granted())
        self.assertFalse(sharedReq2.granted())
        self.assertFalse(exclusiveReq1.granted())
        self.assertFalse(sharedReq3.granted())
        self.assertFalse(sharedReq4.granted())

        exclusiveReq1.cancel()
        resources.pop()

        self.assertFalse(sharedReq1.granted())
        self.assertFalse(sharedReq2.granted())
        self.assertFalse(exclusiveReq1.granted())
        self.assertTrue(exclusiveReq1.canceled())
        self.assertFalse(sharedReq3.granted())
        self.assertFalse(sharedReq4.granted())

        exclusive1.release()
        self.assertTrue(sharedReq1.granted())
        self.assertTrue(sharedReq2.granted())
        self.assertTrue(sharedReq3.granted())
        self.assertTrue(sharedReq4.granted())

        while len(resources) > 0:
            resources.pop().release()

    @MonkeyPatch(rm, "_manager", manager())
    def testCrashOnSwitch(self):
        self.testResourceLockSwitch("crashy")

    @MonkeyPatch(rm, "_manager", manager())
    def testResourceLockSwitch(self, namespace="string"):
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

        self.assertEqual(exclusive1.read(), "resource:exclusive")
        exclusive1.release()
        self.assertEqual(resources[-1].read(), "resource:shared")
        resources.pop().release()
        self.assertEqual(resources[-1].read(), "")
        resources.pop().release()
        self.assertEqual(resources[-1].read(), "resource:exclusive")
        resources.pop().release()
        self.assertEqual(resources[-1].read(), "")
        resources.pop().release()
        self.assertEqual(resources[-1].read(), "resource:shared")
        resources.pop().release()
        # This part is to stop pyflakes for complaining, the reason I need the
        # resourcesRefs alive is so that the manage will not autocollect during
        # the test
        hash(sharedReq1)
        hash(sharedReq2)
        hash(sharedReq3)
        hash(exclusive2)
        hash(exclusive3)
        hash(sharedReq3)

    @MonkeyPatch(rm, "_manager", manager())
    def testResourceAcquireTimeout(self):
        exclusive1 = rm.acquireResource("string", "resource", rm.EXCLUSIVE)
        self.assertRaises(rm.RequestTimedOutError,
                          rm.acquireResource, "string", "resource",
                          rm.EXCLUSIVE, 1)
        exclusive1.release()

    @MonkeyPatch(rm, "_manager", manager())
    def testResourceAcquireInvalidTimeout(self):
        self.assertRaises(TypeError, rm.acquireResource, "string",
                          "resource", rm.EXCLUSIVE, "A")

    @MonkeyPatch(rm, "_manager", manager())
    def testResourceInvalidation(self):
        resource = rm.acquireResource("string", "test", rm.EXCLUSIVE)
        try:
            resource.write("dsada")
        except:
            self.fail()
        resource.release()
        self.assertRaises(Exception, resource.write, "test")

    @MonkeyPatch(rm, "_manager", manager())
    def testResourceAutorelease(self):
        self.log.info("Acquiring resource", extra={'resource': "bob"})
        res = rm.acquireResource("storage", "resource", rm.SHARED)
        resProxy = proxy(res)
        res = None
        # wait for object to die
        self.log.info("Waiting for request")
        try:
            while True:
                resProxy.granted()
        except:
            pass
        self.log.info("Waiting for autoclean")
        while True:
            resStatus = rm._getResourceStatus("storage", "resource")
            if resStatus == rm.LockState.free:
                break
            time.sleep(1)

    @MonkeyPatch(rm, "_manager", manager())
    def testAcquireResourceShared(self):
        res1 = rm.acquireResource("storage", "resource", rm.SHARED)
        res2 = rm.acquireResource("storage", "resource", rm.SHARED, 10)

        res1.release()
        res2.release()

    @MonkeyPatch(rm, "_manager", manager())
    def testResourceStatuses(self):
        self.assertEqual(rm._getResourceStatus("storage", "resource"),
                         rm.LockState.free)
        exclusive1 = rm.acquireResource("storage", "resource", rm.EXCLUSIVE)
        self.assertEqual(rm._getResourceStatus("storage", "resource"),
                         rm.LockState.locked)
        exclusive1.release()
        shared1 = rm.acquireResource("storage", "resource", rm.SHARED)
        self.assertEqual(rm._getResourceStatus("storage", "resource"),
                         rm.LockState.shared)
        shared1.release()
        try:
            self.assertEqual(rm._getResourceStatus("null", "resource"),
                             rm.LockState.free)
        except KeyError:
            return

        self.fail("Managed to get status on a non existing resource")

    @MonkeyPatch(rm, "_manager", manager())
    def testAcquireNonExistingResource(self):
        try:
            rm.acquireResource("null", "resource", rm.EXCLUSIVE)
        except KeyError:
            return

        self.fail("Managed to get status on a non existing resource")

    @MonkeyPatch(rm, "_manager", manager())
    def testAcquireResourceExclusive(self):
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

        self.assertFalse(sharedReq1.granted())
        self.assertFalse(sharedReq2.granted())
        self.assertFalse(exclusiveReq1.granted())
        self.assertFalse(exclusiveReq2.granted())
        exclusive1.release()

        self.assertTrue(sharedReq1.granted())
        self.assertTrue(sharedReq2.granted())
        self.assertFalse(exclusiveReq1.granted())
        self.assertFalse(exclusiveReq2.granted())
        resources.pop().release()  # Shared 1

        self.assertFalse(exclusiveReq1.granted())
        self.assertFalse(exclusiveReq2.granted())
        resources.pop().release()  # Shared 2

        self.assertTrue(exclusiveReq1.granted())
        self.assertFalse(exclusiveReq2.granted())
        resources.pop().release()  # exclusiveReq 1

        self.assertTrue(exclusiveReq2.granted())
        resources.pop().release()  # exclusiveReq 2

    @MonkeyPatch(rm, "_manager", manager())
    def testCancelRequest(self):
        resources = []

        def callback(req, res):
            resources.append(res)

        exclusiveReq1 = rm._registerResource(
            "storage", "resource", rm.EXCLUSIVE, callback)
        exclusiveReq2 = rm._registerResource(
            "storage", "resource", rm.EXCLUSIVE, callback)
        exclusiveReq3 = rm._registerResource(
            "storage", "resource", rm.EXCLUSIVE, callback)

        self.assertTrue(exclusiveReq1.granted())
        self.assertFalse(exclusiveReq2.canceled())
        self.assertFalse(exclusiveReq3.granted())

        exclusiveReq2.cancel()
        self.assertTrue(exclusiveReq2.canceled())
        self.assertEqual(resources.pop(), None)  # exclusiveReq 2

        resources.pop().release()  # exclusiveReq 1

        self.assertTrue(exclusiveReq3.granted())
        resources.pop().release()  # exclusiveReq 3

    @MonkeyPatch(rm, "_manager", manager())
    @pytest.mark.slow
    @pytest.mark.stress
    def testStressTest(self):
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
            self.assertEqual(req.lockType, rm.SHARED)
            res.release()
            threadLimit.release()

        def releaseUnknown(req, res):
            res.release()
            threadLimit.release()

        rnd = Random()

        lockTranslator = [rm.EXCLUSIVE, rm.SHARED]

        threads = []
        for i in range(procLimit / 2):
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


@expandPermutations
class TestResourceManagerLock(VdsmTestCase):

    def test_properties(self):
        a = rm.ResourceManagerLock('ns', 'name', 'mode')
        self.assertEqual('ns', a.ns)
        self.assertEqual('name', a.name)
        self.assertEqual('mode', a.mode)

    @permutations((
        (('nsA', 'nameA', 'mode'), ('nsB', 'nameA', 'mode')),
        (('nsA', 'nameA', 'mode'), ('nsA', 'nameB', 'mode')),
    ))
    def test_less_than(self, a, b):
        b = rm.ResourceManagerLock(*b)
        a = rm.ResourceManagerLock(*a)
        self.assertLess(a, b)

    def test_equality(self):
        a = rm.ResourceManagerLock('ns', 'name', 'mode')
        b = rm.ResourceManagerLock('ns', 'name', 'mode')
        self.assertEqual(a, b)

    def test_mode_used_for_equality(self):
        a = rm.ResourceManagerLock('nsA', 'nameA', 'modeA')
        b = rm.ResourceManagerLock('nsA', 'nameA', 'modeB')
        self.assertNotEqual(a, b)

    def test_mode_ignored_for_sorting(self):
        a = rm.ResourceManagerLock('nsA', 'nameA', 'modeA')
        b = rm.ResourceManagerLock('nsA', 'nameA', 'modeB')
        self.assertFalse(a < b)
        self.assertFalse(b < a)

    @MonkeyPatch(rm, "_manager", FakeResourceManager())
    def test_acquire_release(self):
        lock = rm.ResourceManagerLock('ns_A', 'name_A', rm.SHARED)
        expected = []
        lock.acquire()
        expected.append(('acquireResource',
                         (lock.ns, lock.name, lock.mode),
                         {"timeout": None}))
        self.assertEqual(expected, rm._manager.__calls__)
        lock.release()
        expected.append(('releaseResource', (lock.ns, lock.name), {}))
        self.assertEqual(expected, rm._manager.__calls__)

    def test_repr(self):
        mode = rm.SHARED
        lock = rm.ResourceManagerLock('ns', 'name', mode)
        lock_string = str(lock)
        self.assertIn("ResourceManagerLock", lock_string)
        self.assertIn("ns=ns", lock_string)
        self.assertIn("name=name", lock_string)
        self.assertIn("mode=" + mode, lock_string)
        self.assertIn("%x" % id(lock), lock_string)
