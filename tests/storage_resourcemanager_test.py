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
from thread import error as ThreadError
from StringIO import StringIO
import types
from resource import getrlimit, RLIMIT_NPROC

import storage.resourceManager as resourceManager
from storagefakelib import FakeResourceManager
from testlib import expandPermutations, permutations
from testlib import VdsmTestCase as TestCaseBase
from testValidation import slowtest, stresstest


class NullResourceFactory(resourceManager.SimpleResourceFactory):
    """
    A resource factory that has no resources. Used for testing.
    """
    def resourceExists(self, name):
        return False


class ErrorResourceFactory(resourceManager.SimpleResourceFactory):
    """
    A resource factory that has no resources. Used for testing.
    """
    def createResource(self, name, lockType):
        raise Exception("EPIC FAIL!! LOLZ!!")


class StringResourceFactory(resourceManager.SimpleResourceFactory):
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


class SwitchFailFactory(resourceManager.SimpleResourceFactory):
    def createResource(self, name, lockType):
        s = StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        def switchLockType(self, lockType):
            raise Exception("I NEVER SWITCH!!!")

        s.switchLockType = types.MethodType(switchLockType, s, StringIO)
        return s


class CrashOnCloseFactory(resourceManager.SimpleResourceFactory):
    def createResource(self, name, lockType):
        s = StringIO("%s:%s" % (name, lockType))
        s.seek(0)

        def close(self):
            raise Exception("I NEVER CLOSE!!!")

        s.close = types.MethodType(close, s, StringIO)
        return s


class FailAfterSwitchFactory(resourceManager.SimpleResourceFactory):
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


class ResourceManagerTests(TestCaseBase):
    def setUp(self):
        manager = self.manager = resourceManager.ResourceManager.getInstance()
        manager.registerNamespace("storage",
                                  resourceManager.SimpleResourceFactory())
        manager.registerNamespace("null", NullResourceFactory())
        manager.registerNamespace("string", StringResourceFactory())
        manager.registerNamespace("error", ErrorResourceFactory())
        manager.registerNamespace("switchfail", SwitchFailFactory())
        manager.registerNamespace("crashy", CrashOnCloseFactory())
        manager.registerNamespace("failAfterSwitch", FailAfterSwitchFactory())

    def testErrorInFactory(self):
        manager = self.manager
        req = manager.registerResource("error", "resource",
                                       resourceManager.EXCLUSIVE,
                                       lambda req, res: 1)
        self.assertTrue(req.canceled())

    def testSingleton(self):
        a = resourceManager.ResourceManager.getInstance()
        b = resourceManager.ResourceManager.getInstance()
        self.assertEquals(id(a), id(b))

    def testRegisterInvalidNamespace(self):
        manager = self.manager
        try:
            manager.registerNamespace("I.HEART.DOTS",
                                      resourceManager.SimpleResourceFactory())
        except ValueError:
            return

        self.fail("Managed to register an invalid namespace")

    def testFailCreateAfterSwitch(self):
        resources = []

        def callback(req, res):
            resources.append(res)

        manager = self.manager
        exclusive1 = manager.acquireResource(
            "failAfterSwitch", "resource", resourceManager.EXCLUSIVE)
        sharedReq1 = manager.registerResource(
            "failAfterSwitch", "resource", resourceManager.SHARED, callback)
        exclusive1.release()
        self.assertTrue(sharedReq1.canceled())
        self.assertEquals(resources[0], None)

    def testReregisterNamespace(self):
        manager = self.manager
        self.assertRaises((ValueError, KeyError), manager.registerNamespace,
                          "storage", resourceManager.SimpleResourceFactory())

    def testResourceSwitchLockTypeFail(self):
        self.testResourceLockSwitch("switchfail")

    def testRequestInvalidResource(self):
        manager = self.manager
        self.assertRaises(ValueError, manager.acquireResource,
                          "storage", "DOT.DOT", resourceManager.SHARED)
        self.assertRaises(ValueError, manager.acquireResource,
                          "DOT.DOT", "resource", resourceManager.SHARED)

    def testReleaseInvalidResource(self):
        manager = self.manager
        self.assertRaises(ValueError, manager.releaseResource,
                          "DONT_EXIST", "resource")
        self.assertRaises(ValueError, manager.releaseResource, "storage",
                          "DOT")

    def testResourceWrapper(self):
        manager = self.manager
        s = StringIO
        with manager.acquireResource(
                "string", "test",
                resourceManager.EXCLUSIVE) as resource:
            for attr in dir(s):
                if attr == "close":
                    continue
                self.assertTrue(hasattr(resource, attr))

    def testAccessAttributeNotExposedByWrapper(self):
        manager = self.manager
        with manager.acquireResource(
                "string", "test",
                resourceManager.EXCLUSIVE) as resource:
            try:
                resource.THERE_IS_NO_WAY_I_EXIST
            except AttributeError:
                return
            except Exception as ex:
                self.fail("Wrong exception was raised. "
                          "Expected AttributeError got %s",
                          ex.__class__.__name__)

        self.fail("Managed to access an attribute not exposed by wrapper")

    def testAccessAttributeNotExposedByRequestRef(self):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        manager = self.manager
        req = manager.registerResource(
            "string", "resource", resourceManager.SHARED, callback)
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

    def testRequestRefStr(self):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        manager = self.manager
        req = manager.registerResource(
            "string", "resource", resourceManager.SHARED, callback)
        try:
            str(req)
        finally:
            req.wait()
            resources[0].release()

    def testRequestRefCmp(self):
        resources = []
        requests = []

        def callback(req, res):
            resources.insert(0, res)
            requests.insert(0, req)

        manager = self.manager
        req1 = manager.registerResource(
            "string", "resource", resourceManager.EXCLUSIVE, callback)
        req2 = manager.registerResource(
            "string", "resource", resourceManager.EXCLUSIVE, callback)

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

    def testRequestRecancel(self):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        manager = self.manager
        blocker = manager.acquireResource("string", "resource",
                                          resourceManager.EXCLUSIVE)
        req = manager.registerResource(
            "string", "resource", resourceManager.EXCLUSIVE, callback)

        req.cancel()

        self.assertRaises(resourceManager.RequestAlreadyProcessedError,
                          req.cancel)

        blocker.release()

    def testRequestRegrant(self):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        req = resourceManager.Request(
            "namespace", "name", resourceManager.EXCLUSIVE, callback)
        req.grant()
        self.assertRaises(resourceManager.RequestAlreadyProcessedError,
                          req.grant)

    def testRequestWithBadCallbackOnCancel(self):
        def callback(req, res):
            raise Exception("BUY MILK!")

        manager = self.manager
        blocker = manager.acquireResource("string", "resource",
                                          resourceManager.EXCLUSIVE)
        req = manager.registerResource(
            "string", "resource", resourceManager.EXCLUSIVE, callback)

        req.cancel()

        blocker.release()

    def testRequestWithBadCallbackOnGrant(self):
        def callback(req, res):
            res.release()
            raise Exception("BUY MILK!")

        manager = self.manager
        req = manager.registerResource(
            "string", "resource", resourceManager.EXCLUSIVE, callback)
        req.wait()

    def testRereleaseResource(self):
        manager = self.manager
        res = manager.acquireResource("string", "resource",
                                      resourceManager.EXCLUSIVE)
        res.release()
        res.release()

    def testCancelExclusiveBetweenShared(self):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        manager = self.manager
        exclusive1 = manager.acquireResource(
            "string", "resource", resourceManager.EXCLUSIVE)
        sharedReq1 = manager.registerResource(
            "string", "resource", resourceManager.SHARED, callback)
        sharedReq2 = manager.registerResource(
            "string", "resource", resourceManager.SHARED, callback)
        exclusiveReq1 = manager.registerResource(
            "string", "resource", resourceManager.EXCLUSIVE, callback)
        sharedReq3 = manager.registerResource(
            "string", "resource", resourceManager.SHARED, callback)
        sharedReq4 = manager.registerResource(
            "string", "resource", resourceManager.SHARED, callback)

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

    def testCrashOnSwitch(self):
        self.testResourceLockSwitch("crashy")

    def testResourceLockSwitch(self, namespace="string"):
        resources = []

        def callback(req, res):
            resources.insert(0, res)

        manager = self.manager
        exclusive1 = manager.acquireResource(
            namespace, "resource", resourceManager.EXCLUSIVE)
        sharedReq1 = manager.registerResource(
            namespace, "resource", resourceManager.SHARED, callback)
        sharedReq2 = manager.registerResource(
            namespace, "resource", resourceManager.SHARED, callback)
        exclusive2 = manager.registerResource(
            namespace, "resource", resourceManager.EXCLUSIVE,
            callback)
        exclusive3 = manager.registerResource(
            namespace, "resource", resourceManager.EXCLUSIVE,
            callback)
        sharedReq3 = manager.registerResource(
            namespace, "resource", resourceManager.SHARED, callback)

        self.assertEquals(exclusive1.read(), "resource:exclusive")
        exclusive1.release()
        self.assertEquals(resources[-1].read(), "resource:shared")
        resources.pop().release()
        self.assertEquals(resources[-1].read(), "")
        resources.pop().release()
        self.assertEquals(resources[-1].read(), "resource:exclusive")
        resources.pop().release()
        self.assertEquals(resources[-1].read(), "")
        resources.pop().release()
        self.assertEquals(resources[-1].read(), "resource:shared")
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

    def testResourceAcquireTimeout(self):
        manager = self.manager
        exclusive1 = manager.acquireResource(
            "string", "resource", resourceManager.EXCLUSIVE)
        self.assertRaises(resourceManager.RequestTimedOutError,
                          manager.acquireResource, "string", "resource",
                          resourceManager.EXCLUSIVE, 1)
        exclusive1.release()

    def testResourceAcquireInvalidTimeout(self):
        manager = self.manager
        self.assertRaises(TypeError, manager.acquireResource, "string",
                          "resource", resourceManager.EXCLUSIVE, "A")

    def testResourceInvalidation(self):
        manager = self.manager
        resource = manager.acquireResource("string", "test",
                                           resourceManager.EXCLUSIVE)
        try:
            resource.write("dsada")
        except:
            self.fail()
        resource.release()
        self.assertRaises(Exception, resource.write, "test")

    def testForceRegisterNamespace(self):
        manager = self.manager
        manager.registerNamespace(
            "storage", resourceManager.SimpleResourceFactory(), True)

    def testListNamespaces(self):
        manager = self.manager
        namespaces = manager.listNamespaces()
        self.assertEquals(len(namespaces), 7)

    def testResourceAutorelease(self):
        manager = self.manager
        self.log.info("Acquiring resource", extra={'resource': "bob"})
        res = manager.acquireResource("storage", "resource",
                                      resourceManager.SHARED)
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
            resStatus = manager.getResourceStatus("storage", "resource")
            if resStatus == resourceManager.LockState.free:
                break
            time.sleep(1)

    def testAcquireResourceShared(self):
        manager = self.manager
        res1 = manager.acquireResource("storage", "resource",
                                       resourceManager.SHARED)
        res2 = manager.acquireResource("storage", "resource",
                                       resourceManager.SHARED, 10)

        res1.release()
        res2.release()

    def testResourceStatuses(self):
        manager = self.manager
        self.assertEquals(manager.getResourceStatus("storage", "resource"),
                          resourceManager.LockState.free)
        exclusive1 = manager.acquireResource(
            "storage", "resource", resourceManager.EXCLUSIVE)
        self.assertEquals(manager.getResourceStatus("storage", "resource"),
                          resourceManager.LockState.locked)
        exclusive1.release()
        shared1 = manager.acquireResource("storage", "resource",
                                          resourceManager.SHARED)
        self.assertEquals(manager.getResourceStatus("storage", "resource"),
                          resourceManager.LockState.shared)
        shared1.release()
        try:
            self.assertEquals(manager.getResourceStatus("null", "resource"),
                              resourceManager.LockState.free)
        except KeyError:
            return

        self.fail("Managed to get status on a non existing resource")

    def testAcquireNonExistingResource(self):
        manager = self.manager
        try:
            manager.acquireResource("null", "resource",
                                    resourceManager.EXCLUSIVE)
        except KeyError:
            return

        self.fail("Managed to get status on a non existing resource")

    def testAcquireResourceExclusive(self):
        resources = []

        def callback(req, res):
            resources.append(res)

        manager = self.manager
        exclusive1 = manager.acquireResource(
            "storage", "resource", resourceManager.EXCLUSIVE)
        sharedReq1 = manager.registerResource(
            "storage", "resource", resourceManager.SHARED, callback)
        sharedReq2 = manager.registerResource(
            "storage", "resource", resourceManager.SHARED, callback)
        exclusiveReq1 = manager.registerResource(
            "storage", "resource", resourceManager.EXCLUSIVE,
            callback)
        exclusiveReq2 = manager.registerResource(
            "storage", "resource", resourceManager.EXCLUSIVE,
            callback)

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

    def testCancelRequest(self):
        resources = []

        def callback(req, res):
            resources.append(res)

        manager = self.manager
        exclusiveReq1 = manager.registerResource(
            "storage", "resource", resourceManager.EXCLUSIVE,
            callback)
        exclusiveReq2 = manager.registerResource(
            "storage", "resource", resourceManager.EXCLUSIVE,
            callback)
        exclusiveReq3 = manager.registerResource(
            "storage", "resource", resourceManager.EXCLUSIVE,
            callback)

        self.assertTrue(exclusiveReq1.granted())
        self.assertFalse(exclusiveReq2.canceled())
        self.assertFalse(exclusiveReq3.granted())

        exclusiveReq2.cancel()
        self.assertTrue(exclusiveReq2.canceled())
        self.assertEquals(resources.pop(), None)  # exclusiveReq 2

        resources.pop().release()  # exclusiveReq 1

        self.assertTrue(exclusiveReq3.granted())
        resources.pop().release()  # exclusiveReq 3

    @slowtest
    @stresstest
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
            manager.registerResource(
                "string", "resource", lockTranslator[rnd.randint(0, 1)],
                callback)
            threadLimit.release()

        def releaseShared(req, res):
            self.assertEquals(req.lockType, resourceManager.SHARED)
            res.release()
            threadLimit.release()

        def releaseUnknown(req, res):
            res.release()
            threadLimit.release()

        manager = self.manager
        rnd = Random()

        lockTranslator = [resourceManager.EXCLUSIVE, resourceManager.SHARED]

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

    def tearDown(self):
        manager = self.manager

        manager.unregisterNamespace("null")

        try:
            manager.unregisterNamespace("storage")
            manager.unregisterNamespace("string")
            manager.unregisterNamespace("error")
            manager.unregisterNamespace("switchfail")
            manager.unregisterNamespace("crashy")
            manager.unregisterNamespace("failAfterSwitch")
        except:
            resourceManager.ResourceManager._instance = None
            raise


@expandPermutations
class ResourceManagerLockTest(TestCaseBase):

    def test_properties(self):
        a = resourceManager.ResourceManagerLock('ns', 'name', 'mode')
        self.assertEqual('ns', a.ns)
        self.assertEqual('name', a.name)
        self.assertEqual('mode', a.mode)

    @permutations((
        (('nsA', 'nameA', 'mode'), ('nsB', 'nameA', 'mode')),
        (('nsA', 'nameA', 'mode'), ('nsA', 'nameB', 'mode')),
    ))
    def test_less_than(self, a, b):
        b = resourceManager.ResourceManagerLock(*b)
        a = resourceManager.ResourceManagerLock(*a)
        self.assertLess(a, b)

    def test_equality(self):
        a = resourceManager.ResourceManagerLock('ns', 'name', 'mode')
        b = resourceManager.ResourceManagerLock('ns', 'name', 'mode')
        self.assertEqual(a, b)

    def test_mode_used_for_equality(self):
        a = resourceManager.ResourceManagerLock('nsA', 'nameA', 'modeA')
        b = resourceManager.ResourceManagerLock('nsA', 'nameA', 'modeB')
        self.assertNotEqual(a, b)

    def test_mode_ignored_for_sorting(self):
        a = resourceManager.ResourceManagerLock('nsA', 'nameA', 'modeA')
        b = resourceManager.ResourceManagerLock('nsA', 'nameA', 'modeB')
        self.assertFalse(a < b)
        self.assertFalse(b < a)

    def test_acquire_release(self):
        fake_rm = FakeResourceManager()

        lock = resourceManager.ResourceManagerLock(
            'ns_A', 'name_A', resourceManager.SHARED)
        lock._rm = fake_rm
        expected = []
        lock.acquire()
        expected.append(('acquireResource',
                         (lock.ns, lock.name, lock.mode), {}))
        self.assertEqual(expected, fake_rm.__calls__)
        lock.release()
        expected.append(('releaseResource', (lock.ns, lock.name), {}))
        self.assertEqual(expected, fake_rm.__calls__)

    def test_repr(self):
        mode = resourceManager.SHARED
        lock = resourceManager.ResourceManagerLock('ns', 'name', mode)
        lock_string = str(lock)
        self.assertIn("ResourceManagerLock", lock_string)
        self.assertIn("ns=ns", lock_string)
        self.assertIn("name=name", lock_string)
        self.assertIn("mode=" + mode, lock_string)
        self.assertIn("%x" % id(lock), lock_string)
