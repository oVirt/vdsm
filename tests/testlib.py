#
# Copyright 2012-2014 Red Hat, Inc.
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

import errno
import ConfigParser
import functools
import logging
import os
import pickle
import unittest
from functools import wraps
import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
import xml.etree.ElementTree as ET

from nose import config
from nose import core
from nose import result

import vdsm

from testValidation import SlowTestsPlugin, StressTestsPlugin

# /tmp may use tempfs filesystem, not suitable for some of the test assuming a
# filesystem with direct io support.
TEMPDIR = '/var/tmp'

PERMUTATION_ATTR = "_permutations_"


def dummyTextGenerator(size):
    text = ("Lorem ipsum dolor sit amet, consectetur adipisicing elit, "
            "sed do eiusmod tempor incididunt ut labore et dolore magna "
            "aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
            "ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis "
            "aute irure dolor in reprehenderit in voluptate velit esse cillum "
            "dolore eu fugiat nulla pariatur. Excepteur sint occaecat "
            "cupidatat non proident, sunt in culpa qui officia deserunt "
            "mollit anim id est laborum. ")
    d, m = divmod(size, len(text))
    return (text * d) + text[:m]


def _getPermutation(f, args):
    @wraps(f)
    def wrapper(self):
        return f(self, *args)

    return wrapper


def expandPermutations(cls):
    for attr in dir(cls):
        f = getattr(cls, attr)
        if not hasattr(f, PERMUTATION_ATTR):
            continue

        perm = getattr(f, PERMUTATION_ATTR)
        for args in perm:
            argsString = ", ".join(repr(s) for s in args)
            permName = "%s(%s)" % (f.__name__, argsString)
            wrapper = _getPermutation(f, args)
            wrapper.__name__ = permName
            delattr(wrapper, PERMUTATION_ATTR)
            setattr(cls, permName, wrapper)

        delattr(cls, f.__name__)

    return cls


def permutations(perms):
    def wrap(func):
        setattr(func, PERMUTATION_ATTR, perms)
        return func

    return wrap


class TermColor(object):
    black = 30
    red = 31
    green = 32
    yellow = 33
    blue = 34
    magenta = 35
    cyan = 36
    white = 37


def colorWrite(stream, text, color):
    if os.isatty(stream.fileno()) or os.environ.get("NOSE_COLOR", False):
        stream.write('\x1b[%s;1m%s\x1b[0m' % (color, text))
    else:
        stream.write(text)


@contextmanager
def temporaryPath(perms=None, data=None, dir=TEMPDIR):
    fd, src = tempfile.mkstemp(dir=dir)
    if data is not None:
        f = os.fdopen(fd, "wb")
        f.write(data)
        f.flush()
        f.close()
    else:
        os.close(fd)
    if perms is not None:
        os.chmod(src, perms)
    try:
        yield src
    finally:
        os.unlink(src)


@contextmanager
def namedTemporaryDir(dir=TEMPDIR):
    tmpDir = tempfile.mkdtemp(dir=dir)
    try:
        yield tmpDir
    finally:
        shutil.rmtree(tmpDir)


def make_file(path, size=0):
    dirname = os.path.dirname(path)
    try:
        os.makedirs(dirname)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    with open(path, "w") as f:
        f.truncate(size)


class VdsmTestCase(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        unittest.TestCase.__init__(self, *args, **kwargs)
        self.log = logging.getLogger(self.__class__.__name__)
        self.maxDiff = None  # disable truncating diff in assert error messages

    def retryAssert(self, *args, **kwargs):
        '''Keep retrying an assertion if AssertionError is raised.
           See function utils.retry for the meaning of the arguments.
        '''
        # the utils module only can be imported correctly after
        # hackVdsmModule() is called. Do not import it at the
        # module level.
        from vdsm.utils import retry
        return retry(expectedException=AssertionError, *args, **kwargs)

    def assertNotRaises(self, callableObj=None, *args, **kwargs):
        # This is required when any exception raised during the call should be
        # considered as a test failure.
        context = not_raises(self)
        if callableObj is None:
            return context
        with context:
            callableObj(*args, **kwargs)

    @contextmanager
    def assertElapsed(self, expected, tolerance=0.5):
        start = vdsm.utils.monotonic_time()

        yield

        elapsed = vdsm.utils.monotonic_time() - start

        if abs(elapsed - expected) > tolerance:
            raise AssertionError("Operation time: %s" % elapsed)


class XMLTestCase(VdsmTestCase):

    def assertXMLEqual(self, xml, expectedXML):
        """
        Assert that xml is equivalent to expected xml, ignoring whitespace
        differences.

        In case of a mismatch, display normalized xmls to make it easier to
        find the differences.
        """
        actual = ET.fromstring(xml)
        indent(actual)
        actualXML = ET.tostring(actual)

        expected = ET.fromstring(expectedXML)
        indent(expected)
        expectedXML = ET.tostring(expected)

        self.assertEqual(actualXML, expectedXML,
                         "XMLs are different:\nActual:\n%s\nExpected:\n%s\n" %
                         (actualXML, expectedXML))


def find_xml_element(xml, match):
    """
    Finds the first element matching match. match may be a tag name or path.
    Returns found element xml.

    path is using the limmited supported xpath syntax:
    https://docs.python.org/2/library/
        xml.etree.elementtree.html#supported-xpath-syntax
    Note that xpath support in Python 2.6 is partial and undocumented.
    """
    elem = ET.fromstring(xml)
    found = elem.find(match)
    if found is None:
        raise AssertionError("No such element: %s" % match)
    return ET.tostring(found)


def indent(elem, level=0, s="    "):
    """
    Modify elem indentation in-place.

    Based on http://effbot.org/zone/element-lib.htm#prettyprint
    """
    i = "\n" + level * s
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + s
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level + 1, s)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


class VdsmTestResult(result.TextTestResult):
    def __init__(self, *args, **kwargs):
        result.TextTestResult.__init__(self, *args, **kwargs)
        self._last_case = None

    def _writeResult(self, test, long_result, color, short_result, success):
        if self.showAll:
            colorWrite(self.stream, long_result, color)
            self.stream.writeln()
        elif self.dots:
            self.stream.write(short_result)
            self.stream.flush()

    def addSuccess(self, test):
        unittest.TestResult.addSuccess(self, test)
        self._writeResult(test, 'OK', TermColor.green, '.', True)

    def addFailure(self, test, err):
        unittest.TestResult.addFailure(self, test, err)
        self._writeResult(test, 'FAIL', TermColor.red, 'F', False)

    def addError(self, test, err):
        stream = getattr(self, 'stream', None)
        ec, ev, tb = err
        try:
            exc_info = self._exc_info_to_string(err, test)
        except TypeError:
            # 2.3 compat
            exc_info = self._exc_info_to_string(err)
        for cls, (storage, label, isfail) in self.errorClasses.items():
            if result.isclass(ec) and issubclass(ec, cls):
                if isfail:
                    test.passed = False
                storage.append((test, exc_info))
                # Might get patched into a streamless result
                if stream is not None:
                    if self.showAll:
                        message = [label]
                        detail = result._exception_detail(err[1])
                        if detail:
                            message.append(detail)
                        stream.writeln(": ".join(message))
                    elif self.dots:
                        stream.write(label[:1])
                return
        self.errors.append((test, exc_info))
        test.passed = False
        if stream is not None:
            self._writeResult(test, 'ERROR', TermColor.red, 'E', False)

    def startTest(self, test):
        unittest.TestResult.startTest(self, test)
        current_case = "%s.%s" % (test.test.__module__,
                                  test.test.__class__.__name__)

        if self.showAll:
            if current_case != self._last_case:
                self.stream.writeln(current_case)
                self._last_case = current_case

            self.stream.write(
                '    %s' % str(test.test._testMethodName).ljust(60))
            self.stream.flush()


@contextmanager
def not_raises(test_case):
    try:
        yield
    except Exception as e:
        raise test_case.failureException("Exception raised: %s" % e)


class AssertingLock(object):
    """
    Lock that raises when trying to acquire a locked lock.
    """
    def __init__(self):
        self._lock = threading.Lock()

    def __enter__(self):
        if not self._lock.acquire(False):
            raise AssertionError("Lock is already locked")
        return self

    def __exit__(self, *args):
        self._lock.release()


class VdsmTestRunner(core.TextTestRunner):

    def _makeResult(self):
        return VdsmTestResult(self.stream,
                              self.descriptions,
                              self.verbosity,
                              self.config)


def run():
    argv = sys.argv
    stream = sys.stdout
    verbosity = 3
    testdir = os.path.dirname(os.path.abspath(__file__))

    conf = config.Config(stream=stream,
                         env=os.environ,
                         verbosity=verbosity,
                         workingDir=testdir,
                         plugins=core.DefaultPluginManager())
    conf.plugins.addPlugin(SlowTestsPlugin())
    conf.plugins.addPlugin(StressTestsPlugin())

    runner = VdsmTestRunner(stream=conf.stream,
                            verbosity=conf.verbosity,
                            config=conf)

    sys.exit(not core.run(config=conf, testRunner=runner, argv=argv))


def make_config(tunables):
    """
    Create a vdsm.config.config clone, modified by tunables
    tunables is a list of (section, key, val) tuples
    """
    cfg = ConfigParser.ConfigParser()
    vdsm.config.set_defaults(cfg)
    for (section, key, value) in tunables:
        cfg.set(section, key, value)
    return cfg


def recorded(meth):
    """
    Method decorator recording calls to instance's __recording__ list.
    """
    @wraps(meth)
    def wrapper(self, *args, **kwargs):
        recording = self.__dict__.setdefault("__recording__", [])
        recording.append((meth.func_name, args, kwargs))
        return meth(self, *args, **kwargs)
    return wrapper


def forked(f):
    """
    Decorator for running a test in a child process. Excpetions in the child
    process will be re-raised in the parent.
    """
    @functools.wraps(f)
    def wrapper(*a, **kw):
        r, w = os.pipe()
        try:
            pid = os.fork()
            if pid == 0:
                try:
                    f(*a, **kw)
                    os._exit(0)
                except Exception as e:
                    os.write(w, pickle.dumps(e))
                    os._exit(1)
            else:
                _, status = os.waitpid(pid, 0)
                if status != 0:
                    e = pickle.loads(os.read(r, 4006))
                    raise e
        finally:
            os.close(r)
            os.close(w)

    return wrapper


def online_cpus():
    return frozenset(range(os.sysconf('SC_NPROCESSORS_ONLN')))
