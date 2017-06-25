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
from __future__ import print_function
import os
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

from vdsm import cmdutils
from vdsm import commands
from vdsm.common.proc import pidstat
from vdsm.storage import fileUtils
from vdsm.storage import misc
from vdsm.storage import outOfProcess as oop

from monkeypatch import MonkeyPatch
from testValidation import checkSudo

EXT_DD = "/bin/dd"

EXT_CAT = "cat"
EXT_ECHO = "echo"
EXT_PYTHON = "python"
EXT_SLEEP = "sleep"
EXT_WHOAMI = "whoami"

SUDO_USER = "root"


class TestEvent(VdsmTestCase):

    def testEmit(self):
        ev = threading.Event()

        def callback():
            self.log.info("Callback called")
            ev.set()

        event = misc.Event("EndOfTheWorld")
        event.register(callback)
        event.emit()
        ev.wait(5)
        self.assertTrue(ev.isSet())

    def testEmitStale(self):
        ev = threading.Event()
        callback = lambda: ev.set()
        event = misc.Event("EndOfTheWorld")
        event.register(callback)
        del callback
        event.emit()
        ev.wait(5)
        self.assertFalse(ev.isSet())

    def testUnregister(self):
        ev = threading.Event()
        callback = lambda: ev.set()
        event = misc.Event("EndOfTheWorld")
        event.register(callback)
        event.unregister(callback)
        event.emit()
        ev.wait(5)
        self.assertFalse(ev.isSet())

    def testOneShot(self):
        ev = threading.Event()

        def callback():
            self.log.info("Callback called")
            ev.set()

        event = misc.Event("EndOfTheWorld")
        event.register(callback, oneshot=True)
        event.emit()
        ev.wait(5)
        self.assertTrue(ev.isSet())
        ev.clear()
        event.emit()
        ev.wait(5)
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
        ev.wait(5)
        self.assertTrue(ev.isSet())

    def testInstanceMethod(self):
        ev = threading.Event()
        event = misc.Event("name", sync=True)
        receiver = Receiver(event, ev)
        print(event._registrar)
        event.emit()
        ev.wait(5)
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
        ev.wait(1)
        self.assertFalse(ev.isSet())


class Receiver(object):

    def __init__(self, event, flag):
        self._callback = partial(Receiver.callback, weakref.proxy(self))
        event.register(self._callback)
        self.flag = flag

    def callback(self):
        self.flag.set()


class TestITMap(VdsmTestCase):

    def testMoreArgsThanThreads(self):
        def dummy(arg):
            time.sleep(0.5)
            return arg
        data = frozenset([1, 2, 3, 4])
        currentTime = time.time()
        # we provide 3 thread slots and the input contain 4 vals, means we
        # need to wait for 1 thread to finish before processing all input.
        ret = frozenset(misc.itmap(dummy, data, 3))
        afterTime = time.time()
        # the time should take at least 0.5sec to wait for 1 of the first 3 to
        # finish and another 0.5sec for the last operation,
        # not more than 2 seconds (and I'm large here..)
        self.assertFalse(afterTime - currentTime > 2,
                         msg=("Operation took too long (more than 2 second). "
                              "starts: %s ends: %s") %
                         (currentTime, afterTime))
        # Verify the operation waits at least for 1 thread to finish
        self.assertFalse(afterTime - currentTime < 1,
                         msg="Operation was too fast, not all threads were "
                             "initiated as desired (with 1 thread delay)")
        self.assertEqual(ret, data)

    def testMaxAvailableProcesses(self):
        def dummy(arg):
            return arg
        # here we launch the maximum threads we can initiate in every
        # outOfProcess operation + 1. it let us know that oop and itmap operate
        # properly with their limitations
        data = frozenset(range(oop.HELPERS_PER_DOMAIN + 1))
        ret = frozenset(misc.itmap(dummy, data, misc.UNLIMITED_THREADS))
        self.assertEqual(ret, data)

    def testMoreThreadsThanArgs(self):
        data = [1]
        self.assertEqual(list(misc.itmap(int, data, 80)), data)

    def testInvalidITMapParams(self):
        data = 1
        self.assertRaises(ValueError, lambda: next(misc.itmap(int, data, 0)))


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


@pytest.mark.skipif(six.PY3, reason="not compatible with python 3")
class TestAsyncProc(VdsmTestCase):

    def test(self):
        data = """Striker: You are a Time Lord, a lord of time.
                           Are there lords in such a small domain?
                  The Doctor: And where do you function?
                  Striker: Eternity. The endless wastes of eternity. """
        # (C) BBC - Doctor Who
        p = commands.execCmd([EXT_CAT], sync=False)
        self.log.info("Writing data to std out")
        p.stdin.write(data)
        p.stdin.flush()
        self.log.info("Written data reading")
        self.assertEqual(p.stdout.read(len(data)), data)

    def testMutiWrite(self):
        data = """The Doctor: Androzani Major was becoming quite developed
                              last time I passed this way.
                  Peri: When was that?
                  The Doctor: ...I don't remember.
                              I'm pretty sure it wasn't the future. """
        # (C) BBC - Doctor Who
        halfPoint = len(data) / 2
        p = commands.execCmd([EXT_CAT], sync=False)
        self.log.info("Writing data to std out")
        p.stdin.write(data[:halfPoint])
        self.log.info("Writing more data to std out")
        p.stdin.write(data[halfPoint:])
        p.stdin.flush()
        self.log.info("Written data reading")
        self.assertEqual(p.stdout.read(len(data)), data)

    def testWriteLargeData(self):
        data = """The Doctor: Davros, if you had created a virus in your
                              laboratory, something contagious and infectious
                              that killed on contact, a virus that would
                              destroy all other forms of life; would you allow
                              its use?
                  Davros: It is an interesting conjecture.
                  The Doctor: Would you do it?
                  Davros: The only living thing... The microscopic organism...
                          reigning supreme... A fascinating idea.
                  The Doctor: But would you do it?
                  Davros: Yes; yes. To hold in my hand, a capsule that
                          contained such power. To know that life and death on
                          such a scale was my choice. To know that the tiny
                          pressure on my thumb, enough to break the glass,
                          would end everything. Yes! I would do it! That power
                          would set me up above the gods! And through the
                          Daleks, I shall have that power! """
        # (C) BBC - Doctor Who

        data = data * 100
        p = commands.execCmd([EXT_CAT], sync=False)
        self.log.info("Writing data to std out")
        p.stdin.write(data)
        p.stdin.flush()
        self.log.info("Written data reading")
        self.assertEqual(p.stdout.read(len(data)), data)

    def testWaitTimeout(self):
        ttl = 2
        p = commands.execCmd([EXT_SLEEP, str(ttl + 10)], sync=False)
        startTime = time.time()
        p.wait(ttl)
        duration = time.time() - startTime
        self.assertTrue(duration < (ttl + 1))
        self.assertTrue(duration > (ttl))
        p.kill()

    def testWaitCond(self):
        ttl = 2
        p = commands.execCmd([EXT_SLEEP, str(ttl + 10)], sync=False)
        startTime = time.time()
        p.wait(cond=lambda: time.time() - startTime > ttl)
        duration = time.time() - startTime
        self.assertTrue(duration < (ttl + 2))
        self.assertTrue(duration > (ttl))
        p.kill()

    def testCommunicate(self):
        data = ("The trouble with the world is that the stupid are cocksure "
                "and the intelligent are full of doubt")
        p = commands.execCmd([EXT_DD], data=data, sync=False)
        p.stdin.close()
        self.assertEqual(p.stdout.read(len(data)).strip(), data)


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
        [("512", 1),
         ("513", 2),
         (u"1073741824", 2097152),
         ])
    def test_valid_size(self, size, result):
        self.assertEqual(misc.validateSize(size, "size"), result)

    @permutations([
        # size
        [2097152],  # 1GiB in sectors
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


class TestUuidPack(VdsmTestCase):

    @pytest.mark.xfail(six.PY3, reason="broken on python 3")
    def test(self):
        """
        Test that the uuid that was packed can be unpacked without being
        changed
        """
        for i in range(1000):
            origUuid = str(uuid.uuid4())
            packedUuid = misc.packUuid(origUuid)
            self.assertEqual(misc.unpackUuid(packedUuid), origUuid)


class TestChecksum(VdsmTestCase):

    def testConsistency(self):
        """
        Test if when given the same input in different times the user will get
        the same checksum.
        """
        with open("/dev/urandom", "rb") as f:
            data = f.read(50)
        self.assertEqual(misc.checksum(data, 16), misc.checksum(data, 16))


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


class TestAlignData(VdsmTestCase):

    def test(self):
        """
        Test various inputs and see that they are correct.
        """
        self.assertEqual(misc._alignData(100, 100), (4, 25, 25))
        self.assertEqual(misc._alignData(512, 512), (512, 1, 1))
        self.assertEqual(misc._alignData(1, 1024), (1, 1, 1024))
        self.assertEqual(misc._alignData(10240, 512), (512, 20, 1))
        self.assertEqual(misc._alignData(1, 1), (1, 1, 1))


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

        self.assertEqual(block[0], expectedResultData)

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
        line = "Hey Scully, is this display of boyish agility " + \
               "turning you on at all?"
        # (C) Fox - The X Files
        code = "import sys; sys.stderr.write('%s')" % line
        ret, stdout, stderr = commands.execCmd([EXT_PYTHON, "-c", code])
        self.assertEqual(stderr[0].decode("ascii"), line)

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
        proc = commands.execCmd(cmd, nice=10, sync=False)
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
