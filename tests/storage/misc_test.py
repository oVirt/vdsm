#
# Copyright 2012-2017 Red Hat, Inc.
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
from __future__ import print_function

import binascii
import os
import random
import tempfile
import uuid
import time
import threading
import weakref

from functools import partial

import pytest
import six

from testlib import AssertingLock
from testlib import VdsmTestCase
from testlib import namedTemporaryDir
from testlib import permutations, expandPermutations
from testlib import TEMPDIR

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common.proc import pidstat
from vdsm.storage import fileUtils
from vdsm.storage import misc

from monkeypatch import MonkeyPatch
from testValidation import checkSudo


EXT_DD = "/bin/dd"

EXT_CAT = "cat"
EXT_ECHO = "echo"
EXT_PYTHON = "python"
EXT_SLEEP = "sleep"
EXT_WHOAMI = "whoami"

SUDO_USER = "root"

# Time to wait for a trivial operation on another thread. This can be low as
# 0.005 second on an idle laptop. Set to higher value so it will not fail too
# quickly on an overloaded nested CI slave.
TIMEOUT = 0.25

# When using wait() on an async command, minimal value seems to be 1.0 seconds.
EXECCMD_TIMEOUT = 1.0


class TestEvent(VdsmTestCase):

    def testEmit(self):
        ev = threading.Event()

        def callback():
            self.log.info("Callback called")
            ev.set()

        event = misc.Event("EndOfTheWorld")
        event.register(callback)
        event.emit()
        ev.wait(TIMEOUT)
        self.assertTrue(ev.isSet())

    def testEmitStale(self):
        ev = threading.Event()
        callback = lambda: ev.set()
        event = misc.Event("EndOfTheWorld")
        event.register(callback)
        del callback
        event.emit()
        ev.wait(TIMEOUT)
        self.assertFalse(ev.isSet())

    def testUnregister(self):
        ev = threading.Event()
        callback = lambda: ev.set()
        event = misc.Event("EndOfTheWorld")
        event.register(callback)
        event.unregister(callback)
        event.emit()
        ev.wait(TIMEOUT)
        self.assertFalse(ev.isSet())

    def testOneShot(self):
        ev = threading.Event()

        def callback():
            self.log.info("Callback called")
            ev.set()

        event = misc.Event("EndOfTheWorld")
        event.register(callback, oneshot=True)
        event.emit()
        ev.wait(TIMEOUT)
        self.assertTrue(ev.isSet())
        ev.clear()
        event.emit()
        ev.wait(TIMEOUT)
        self.assertFalse(ev.isSet())

    def testEmitCallbackException(self):
        ev = threading.Event()

        def callback1():
            raise Exception("AHHHHHHH!!!")

        def callback2():
            ev.set()

        event = misc.Event("EndOfTheWorld", sync=True)
        event.register(callback1)
        event.register(callback2)
        event.emit()
        ev.wait(TIMEOUT)
        self.assertTrue(ev.isSet())

    def testInstanceMethod(self):
        ev = threading.Event()
        event = misc.Event("name", sync=True)
        receiver = Receiver(event, ev)
        print(event._registrar)
        event.emit()
        ev.wait(TIMEOUT)
        self.assertTrue(ev.isSet())
        receiver  # Makes pyflakes happy

    def testInstanceMethodDead(self):
        ev = threading.Event()
        event = misc.Event("name", sync=True)
        receiver = Receiver(event, ev)
        print(event._registrar)
        del receiver
        print(event._registrar)
        event.emit()
        ev.wait(TIMEOUT)
        self.assertFalse(ev.isSet())


class Receiver(object):

    def __init__(self, event, flag):
        self._callback = partial(Receiver.callback, weakref.proxy(self))
        event.register(self._callback)
        self.flag = flag

    def callback(self):
        self.flag.set()


class TestParseHumanReadableSize(VdsmTestCase):

    def testValidInput(self):
        """
        Test that the method parses size correctly if given correct input.
        """
        for i in range(1, 1000):
            for schar, power in [("T", 40), ("G", 30), ("M", 20), ("K", 10)]:
                expected = misc.parseHumanReadableSize("%d%s" % (i, schar))
                self.assertEqual(expected, (2 ** power) * i)

    def testInvalidInput(self):
        """
        Test that parsing handles invalid input correctly
        """
        self.assertEqual(misc.parseHumanReadableSize("T"), 0)
        self.assertEqual(misc.parseHumanReadableSize("TNT"), 0)
        self.assertRaises(AttributeError, misc.parseHumanReadableSize, 5)
        self.assertEqual(misc.parseHumanReadableSize("4.3T"), 0)


class TestValidateN(VdsmTestCase):

    def testValidInput(self):
        """
        Test cases that the validator should validate.
        """
        try:
            value = 1
            misc.validateN(value, "a")
            value = "1"
            misc.validateN(value, "a")
            value = 1.0
            misc.validateN(value, "a")
            value = "471902437190237189236189"
            misc.validateN(value, "a")
        except misc.se.InvalidParameterException:
            self.fail("Failed while validating a valid value '%s'" % value)

    def testInvalidInput(self):
        """
        Test that the validator doesn't validate illegal input.
        """
        expectedException = misc.se.InvalidParameterException
        self.assertRaises(expectedException, misc.validateN, "A", "a")
        self.assertRaises(expectedException, misc.validateN, "-1", "a")
        self.assertRaises(expectedException, misc.validateN, -1, "a")
        self.assertRaises(expectedException, misc.validateN, "4.3", "a")
        self.assertRaises(expectedException, misc.validateN, "", "a")
        self.assertRaises(expectedException, misc.validateN, "*", "a")
        self.assertRaises(expectedException, misc.validateN, "2-1", "a")


class TestValidateInt(VdsmTestCase):

    def testValidInput(self):
        """
        Test cases that the validator should validate.
        """
        try:
            value = 1
            misc.validateInt(value, "a")
            value = -1
            misc.validateInt(value, "a")
            value = "1"
            misc.validateInt(value, "a")
            value = 1.0
            misc.validateInt(value, "a")
            value = "471902437190237189236189"
            misc.validateInt(value, "a")
        except misc.se.InvalidParameterException:
            self.fail("Failed while validating a valid value '%s'" % value)

    def testInvalidInput(self):
        """
        Test that the validator doesn't validate illegal input.
        """
        expectedException = misc.se.InvalidParameterException
        self.assertRaises(expectedException, misc.validateInt, "A", "a")
        self.assertRaises(expectedException, misc.validateInt, "4.3", "a")
        self.assertRaises(expectedException, misc.validateInt, "", "a")
        self.assertRaises(expectedException, misc.validateInt, "*", "a")
        self.assertRaises(expectedException, misc.validateInt, "2-1", "a")


@pytest.mark.skipif(six.PY3, reason="name 'basestring' is not defined")
@expandPermutations
class TestValidateSize(VdsmTestCase):

    @permutations(
        # size, result
        [("512", 512),
         ("513", 513),
         (u"1073741824", 1073741824),
         ])
    def test_valid_size(self, size, result):
        self.assertEqual(misc.validateSize(size, "size"), result)

    @permutations([
        # size
        [2097152],  # 1GiB in blocks
        [1000.14],
        ["one"],
        ["nan"],
        ["3.14"],
        ["-1"],
    ])
    def test_invalid_size(self, size):
        self.assertRaises(misc.se.InvalidParameterException,
                          misc.validateSize, size, "size")


class TestValidateUuid(VdsmTestCase):

    def testValidInput(self):
        """
        Test if the function succeeds in validating valid UUIDs.
        """
        for i in range(1000):
            tmpUuid = str(uuid.uuid4())
            try:
                misc.validateUUID(tmpUuid)
            except misc.se.InvalidParameterException:
                self.fail("Could not parse VALID UUID '%s'" % tmpUuid)

    def testInvalidInputNotHex(self):
        """
        Test that validator detects when a non HEX char is in the input.
        """
        self.assertRaises(misc.se.InvalidParameterException, misc.validateUUID,
                          "Dc08ff668-4072-4191-9fbb-f1c8f2daz333")

    def testInvalidInputInteger(self):
        """
        Test that validator detects when an integer is in the input.
        """
        self.assertRaises(misc.se.InvalidParameterException, misc.validateUUID,
                          23)

    def testInvalidInputUTF(self):
        """
        Test that validator detects encoded utf-8 is in the input
        """
        self.assertRaises(misc.se.InvalidParameterException, misc.validateUUID,
                          u'\xe4\xbd\xa0\xe5\xa5\xbd')

    def testWrongLength(self):
        """
        Test that the validator detects when the input is not in the correct
        length
        """
        self.assertRaises(misc.se.InvalidParameterException, misc.validateUUID,
                          "Dc08ff668-4072-4191-9fbb-f1c8f2daa33")
        self.assertRaises(misc.se.InvalidParameterException, misc.validateUUID,
                          "Dc08ff668-4072-4191-9fb-f1c8f2daa333")
        self.assertRaises(misc.se.InvalidParameterException, misc.validateUUID,
                          "Dc08ff68-4072-4191-9fbb-f1c8f2daa333")
        self.assertRaises(misc.se.InvalidParameterException, misc.validateUUID,
                          "Dc08ff668-4072-4191-9fbb-f1c8f2daa3313")


# Note: packed values were generated by misc.packUuid(), to ensure that we keep
# the packed format.
@pytest.mark.parametrize("bytes,packed", [
    # The smallest possible random value
    pytest.param(
        b"\x00" * 16,
        b"\x00\x00\x00\x00\x00\x00\x00\x80\x00@\x00\x00\x00\x00\x00\x00",
        id="smallest"),
    # The highest possible random value
    pytest.param(
        b"\xff" * 16,
        b"\xff\xff\xff\xff\xff\xff\xff\xbf\xffO\xff\xff\xff\xff\xff\xff",
        id="highest"),
    # "Random" value that should be easy to reason about
    pytest.param(
        b"\x00\x01\x02\x03\x04\x05\x06\x07\x00\x01\x02\x03\x04\x05\x06\x07",
        b"\x07\x06\x05\x04\x03\x02\x01\x80\x07F\x05\x04\x03\x02\x01\x00",
        id="random"),
], ids=binascii.hexlify)
def test_pack_uuid(bytes, packed):
    # Create uuid version 4 - note that this modifies the bytes so variant and
    # version number set according to RFC 4122.
    u = str(uuid.UUID(bytes=bytes, version=4))
    assert misc.packUuid(u) == packed
    assert misc.unpackUuid(packed) == u


def test_pack_uuid_random():
    # Use pseudo random numbers for repeatable tests results.
    r = random.Random(42)
    for i in range(1000):
        u = str(uuid.UUID(int=r.randint(0, 2**128), version=4))
        packed = misc.packUuid(u)
        assert misc.unpackUuid(packed) == u


class TestParseBool(VdsmTestCase):

    def testValidInput(self):
        """
        Compare valid inputs with expected results.
        """
        self.assertEqual(misc.parseBool(True), True)
        self.assertEqual(misc.parseBool(False), False)
        self.assertEqual(misc.parseBool("true"), True)
        self.assertEqual(misc.parseBool("tRue"), True)
        self.assertEqual(misc.parseBool("false"), False)
        self.assertEqual(misc.parseBool("fAlse"), False)
        self.assertEqual(misc.parseBool("BOB"), False)

    def testInvalidInput(self):
        """
        See that the method is consistent when giver invalid input.
        """
        self.assertRaises(AttributeError, misc.parseBool, 1)
        self.assertRaises(AttributeError, misc.parseBool, None)


class TestValidateDDBytes(VdsmTestCase):

    def testValidInputTrue(self):
        """
        Test that it works when given valid and correct input.
        """
        count = 802
        with tempfile.NamedTemporaryFile() as f:
            cmd = [EXT_DD, "bs=1", "if=/dev/urandom", 'of=%s' % f.name,
                   'count=%d' % count]
            rc, out, err = commands.execCmd(cmd)

        self.assertTrue(misc.validateDDBytes(err, count))

    def testValidInputFalse(self):
        """
        Test that is work when given valid but incorrect input.
        """
        count = 802
        with tempfile.NamedTemporaryFile() as f:
            cmd = [EXT_DD, "bs=1", "if=/dev/urandom", 'of=%s' % f.name,
                   'count=%d' % count]
            rc, out, err = commands.execCmd(cmd)

        self.assertFalse(misc.validateDDBytes(err, count + 1))

    def testInvalidInput(self):
        """
        Test that the method handles wired input.
        """
        self.assertRaises(misc.se.InvalidParameterException,
                          misc.validateDDBytes,
                          ["I AM", "PRETENDING TO", "BE DD"], "BE")
        self.assertRaises(misc.se.InvalidParameterException,
                          misc.validateDDBytes,
                          ["I AM", "PRETENDING TO", "BE DD"], 32)


@pytest.mark.skipif(six.PY3, reason="try to write text to binary file")
class TestReadBlock(VdsmTestCase):

    def _createTempFile(self, neededFileSize, writeData):
        """
        Create a temp file with the data in *writeData* written continuously in
        it.

        :returns: the path of the new temp file.
        """
        dataLength = len(writeData)

        fd, path = tempfile.mkstemp(dir=TEMPDIR)
        f = os.fdopen(fd, "wb")

        written = 0
        while written < neededFileSize:
            f.write(writeData)
            written += dataLength
        f.close()
        return path

    def testValidInput(self):
        """
        Test that when all arguments are correct the method works smoothly.
        """
        writeData = "DON'T THINK OF IT AS DYING, said Death." + \
                    "JUST THINK OF IT AS LEAVING EARLY TO AVOID THE RUSH."
        # (C) Terry Pratchet - Good Omens
        dataLength = len(writeData)

        offset = 512
        size = 512

        path = self._createTempFile(offset + size, writeData)

        # Figure out what outcome should be
        timesInSize = int(size / dataLength) + 1
        relOffset = offset % dataLength
        expectedResultData = (writeData * timesInSize)
        expectedResultData = \
            (expectedResultData[relOffset:] + expectedResultData[:relOffset])
        expectedResultData = expectedResultData[:size]
        block = misc.readblock(path, offset, size)

        os.unlink(path)

        self.assertEqual(block, expectedResultData)

    def testInvalidOffset(self):
        """
        Make sure that we check for invalid (non 512 aligned) offset.
        """
        offset = 513
        self.assertRaises(misc.se.MiscBlockReadException, misc.readblock,
                          "/dev/urandom", offset, 512)

    def testInvalidSize(self):
        """
        Make sure that we check for invalid (non 512 aligned) size.
        """
        size = 513
        self.assertRaises(misc.se.MiscBlockReadException, misc.readblock,
                          "/dev/urandom", 512, size)

    def testReadingMoreTheFileSize(self):
        """
        See that correct exception is raised when trying to read more then the
        file has to offer.
        """
        writeData = "History, contrary to popular theories, " + \
                    "is kings and dates and battles."
        # (C) Terry Pratchet - Small Gods

        offset = 512
        size = 512

        path = self._createTempFile(offset + size - 100, writeData)

        self.assertRaises(misc.se.MiscBlockReadIncomplete, misc.readblock,
                          path, offset, size)

        os.unlink(path)


class TestCleanUpDir(VdsmTestCase):

    def testFullDir(self):
        """
        Test if method can clean a dir it should be able to.
        """
        with namedTemporaryDir() as baseDir:
            # Populate dir
            dirty = os.path.join(baseDir, 'dirty')
            os.mkdir(dirty)
            numOfFilesToCreate = 50
            for i in range(numOfFilesToCreate):
                tempfile.mkstemp(dir=dirty)

            # clean it
            fileUtils.cleanupdir(dirty)

            self.assertFalse(os.path.lexists(dirty))

    def testEmptyDir(self):
        """
        Test if method can delete an empty dir.
        """
        with namedTemporaryDir() as baseDir:
            dirty = os.path.join(baseDir, 'dirty')
            os.mkdir(dirty)
            fileUtils.cleanupdir(dirty)
            self.assertFalse(os.path.lexists(dirty))

    def testNotExistingDir(self):
        """
        See that method doesn't throw a fit if given a non existing dir
        """
        fileUtils.cleanupdir(os.path.join("I", " DONT", "EXIST"))

    def testDirWithUndeletableFiles(self):
        """
        See that the method handles correctly a situation where it is given a
        dir it can't clean.
        """
        baseDir = "/proc/misc"  # This can't be deleted

        # Try and fail to clean it
        fileUtils.cleanupdir(baseDir, ignoreErrors=True)
        self.assertTrue(os.path.lexists(baseDir))

        self.assertRaises(RuntimeError, fileUtils.cleanupdir,
                          baseDir, False)
        self.assertTrue(os.path.lexists(baseDir))


class TestPidExists(VdsmTestCase):

    def testPidExists(self):
        """
        Test if pid given exists.
        """
        mypid = os.getpid()

        self.assertTrue(misc.pidExists(mypid))

    def testPidDoesNotExist(self):
        """
        Test if when given incorrect input the method works correctly.
        """
        # FIXME : There is no way real way know what process aren't working.
        # I'll just try and see if there is **any** occasion where it works
        # If anyone has any idea. You are welcome to change this

        pid = os.getpid()
        result = True
        while result:
            pid += 1
            result = misc.pidExists(pid)


class TestExecCmd(VdsmTestCase):

    def testExec(self):
        """
        Tests that execCmd execs and returns the correct ret code
        """
        ret, out, err = commands.execCmd([EXT_ECHO])

        self.assertEqual(ret, 0)

    @MonkeyPatch(cmdutils, "_USING_CPU_AFFINITY", True)
    def testNoCommandWithAffinity(self):
        rc, _, _ = commands.execCmd(["I.DONT.EXIST"])
        self.assertNotEqual(rc, 0)

    @MonkeyPatch(cmdutils, "_USING_CPU_AFFINITY", False)
    def testNoCommandWithoutAffinity(self):
        self.assertRaises(OSError, commands.execCmd, ["I.DONT.EXIST"])

    def testStdOut(self):
        """
        Tests that execCmd correctly returns the standard output of the prog it
        executes.
        """
        line = "All I wanted was to have some pizza, hang out with dad, " + \
               "and not let your weirdness mess up my day"
        # (C) Nickolodeon - Invader Zim
        ret, stdout, stderr = commands.execCmd((EXT_ECHO, line))
        self.assertEqual(stdout[0].decode("ascii"), line)

    def testStdErr(self):
        """
        Tests that execCmd correctly returns the standard error of the prog it
        executes.
        """
        cmd = ["sh", "-c", "echo it works! >&2"]
        ret, stdout, stderr = commands.execCmd(cmd)
        self.assertEqual(stderr[0].decode("ascii"), "it works!")

    def testSudo(self):
        """
        Tests that when running with sudo the user really is root (or other
        desired user).
        """
        cmd = [EXT_WHOAMI]
        checkSudo(cmd)
        ret, stdout, stderr = commands.execCmd(cmd, sudo=True)
        self.assertEqual(stdout[0].decode("ascii"), SUDO_USER)

    @pytest.mark.skipif(six.PY3, reason="uses AsyncProc")
    def testNice(self):
        cmd = ["sleep", "10"]
        proc = commands.start(cmd, nice=10)
        try:
            time.sleep(0.2)
            nice = pidstat(proc.pid).nice
            self.assertEqual(nice, 10)
        finally:
            proc.kill()
            proc.wait()


class TestSamplingMethod(VdsmTestCase):

    # Note: this should be long enough so even on very loaded machine, all
    # threads will start within this delay. If this tests fails randomly,
    # increase this value.
    COMPUTE_SECONDS = 0.2

    def setUp(self):
        # Will raise if more then one thread try to enter the sampling method
        self.single_thread_allowed = AssertingLock()
        self.entered_sampling_method = threading.Event()
        self.results = ['result-1', 'result-2']

    def test_single_thread(self):
        """
        This is just a sanity test that may help to debug issues in the more
        complex tests.
        """
        thread = SamplingThread(self.sampling_method)
        thread.start()
        thread.join()
        self.assertEqual('result-1', thread.result)
        self.assertEqual(['result-2'], self.results)

    def test_two_threads(self):
        """
        The second thread must wait until the first thread is finished before
        it calls the sampling method.
        """
        first_thread = SamplingThread(self.sampling_method)
        second_thread = SamplingThread(self.sampling_method)
        first_thread.start()
        self.entered_sampling_method.wait()
        second_thread.start()
        second_thread.join()
        self.assertEqual('result-1', first_thread.result)
        self.assertEqual('result-2', second_thread.result)
        self.assertEqual([], self.results)

    def test_many_threads(self):
        """
        The other threads must wait until the first thread is finished before
        they enter the sampling method. Then, one of the other threads will
        call the sampling method, and all of them will return the result
        obtained in this call.
        """
        first_thread = SamplingThread(self.sampling_method)
        others = []
        for i in range(3):
            thread = SamplingThread(self.sampling_method)
            others.append(thread)
        first_thread.start()
        self.entered_sampling_method.wait()
        for thread in others:
            thread.start()
        for thread in others:
            thread.join()
        self.assertEqual('result-1', first_thread.result)
        for thread in others:
            self.assertEqual('result-2', thread.result)
        self.assertEqual([], self.results)

    @misc.samplingmethod
    def sampling_method(self):
        with self.single_thread_allowed:
            self.entered_sampling_method.set()
            time.sleep(self.COMPUTE_SECONDS)
            return self.results.pop(0)


class SamplingThread(object):

    def __init__(self, func):
        self._func = func
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self.result = None

    def start(self):
        self._thread.start()

    def join(self):
        self._thread.join()

    def _run(self):
        self.result = self._func()


class TestDynamicBarrier(VdsmTestCase):

    def test_exit_without_enter(self):
        barrier = misc.DynamicBarrier()
        self.assertRaises(AssertionError, barrier.exit)

    def test_enter_and_exit(self):
        barrier = misc.DynamicBarrier()
        self.assertTrue(barrier.enter())
        barrier.exit()


@pytest.mark.parametrize("check_str,result", [
    pytest.param(
        u"The quick brown fox jumps over the lazy dog.", True, id="Ascii"),
    pytest.param(u"\u05d0", False, id="Unicode"),
    pytest.param(u"", True, id="Empty"),
])
def test_isAscii(check_str, result):
    assert misc.isAscii(check_str) == result


@pytest.mark.skipif(
    six.PY2, reason="Bytes support both encode and decode calls")
def test_checkBytes():
    with pytest.raises(AttributeError):
        misc.isAscii(b"bytes")


@pytest.mark.parametrize("length, offset, expected", [
    (100, 100, (4, 25, 25)),
    (512, 512, (512, 1, 1)),
    (1, 1024, (1, 1, 1024)),
    (10240, 512, (512, 20, 1)),
    (1, 1, (1, 1, 1)),
])
def test_align_data(length, offset, expected):
    alignment = misc._alignData(length, offset)
    assert alignment == expected
    for value in alignment:
        assert isinstance(value, int)
