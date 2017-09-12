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
import errno
import functools
import inspect
import io
import logging
import os
import pickle
import platform
import unittest
import uuid
from functools import wraps
import shutil
import sys
from six.moves import configparser
from six.moves import range
import tempfile
import threading
import time
from contextlib import contextmanager
import xml.etree.ElementTree as ET

try:
    from unittest import mock
except ImportError:  # py2
    import mock
mock

from nose import config
from nose import core
from nose import result

import vdsm

from vdsm.common import cache
from vdsm.common import osutils
from vdsm.common import xmlutils
import vdsm.common.time
from vdsm.virt import vmxml

from monkeypatch import Patch
from testValidation import (
    SlowTestsPlugin,
    StressTestsPlugin,
    ThreadLeakPlugin,
    ProcessLeakPlugin,
    FileLeakPlugin,
)

# /tmp may use tempfs filesystem, not suitable for some of the test assuming a
# filesystem with direct io support.
TEMPDIR = '/var/tmp'

PERMUTATION_ATTR = "_permutations_"

_ARCH_REAL = platform.machine()
_ARCH_FAKE = 'x86_64'


class Sigargs(object):

    def __init__(self, func):
        try:
            from inspect import signature
            self._py3 = True
        except ImportError:  # py2
            from inspect import getargspec as signature
            self._py3 = False
        self._sig = signature(func)

    @property
    def args(self):
        if self._py3:
            args = ['self']
            args.extend([arg.name for arg in self._sig.parameters.values()])
            return args
        else:
            return self._sig.args

    @property
    def varargs(self):
        if self._py3:
            varargs = [arg.name for arg in self._sig.parameters.values()
                       if arg.kind == arg.VAR_KEYWORD]
            return None if varargs == [] else varargs
        else:
            return self._sig.varargs

    @property
    def keywords(self):
        if self._py3:
            keywords = [arg.name for arg in self._sig.parameters.values()
                        if arg.kind == arg.KEYWORD_ONLY]
            return None if keywords == [] else keywords
        else:
            return self._sig.keywords

    @property
    def defaults(self):
        if self._py3:
            return [arg.name for arg in self._sig.parameters.values()
                    if arg.default]
        else:
            return self._sig.defaults


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
            # pytest does not support "[" in permuted test name
            argsString = argsString.replace("[", "(").replace("]", ")")
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
        with io.open(fd, "wb") as f:
            f.write(data)
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


def _vdsm_machine():
    return (
        _ARCH_REAL if _ARCH_REAL in (
            # FIXME: this duplicates caps.Architecture, but
            # we cannot import caps.py in this module.
            'x86_64', 'ppc64', 'ppc64le'
        ) else _ARCH_FAKE
    )


class VdsmTestCase(unittest.TestCase):

    _patch_arch = Patch([
        (platform, "machine", _vdsm_machine),
    ])

    def __init__(self, *args, **kwargs):
        unittest.TestCase.__init__(self, *args, **kwargs)
        self.log = logging.getLogger(self.__class__.__name__)
        self.maxDiff = None  # disable truncating diff in assert error messages

    @classmethod
    def setUpClass(cls):
        cls._patch_arch.apply()

    @classmethod
    def tearDownClass(cls):
        cls._patch_arch.revert()

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
        start = vdsm.common.time.monotonic_time()

        yield

        elapsed = vdsm.common.time.monotonic_time() - start

        if abs(elapsed - expected) > tolerance:
            raise AssertionError("Operation time: %s" % elapsed)

    def assertEquals(self, *args, **kwargs):
        raise RuntimeError(
            "assertEquals is deprecated, please use assertEqual\n"
            "See https://docs.python.org/2/library/unittest.html"
            "#deprecated-aliases")


class XMLTestCase(VdsmTestCase):

    def assertXMLEqual(self, xml, expectedXML):
        """
        Assert that xml is equivalent to expected xml, ignoring whitespace
        differences.

        In case of a mismatch, display normalized xmls to make it easier to
        find the differences.
        """
        actual = ET.fromstring(xml)
        xmlutils.indent(actual)
        actualXML = ET.tostring(actual)

        expected = ET.fromstring(expectedXML)
        xmlutils.indent(expected)
        expectedXML = ET.tostring(expected)

        self.assertEqual(actualXML, expectedXML,
                         "XMLs are different:\nActual:\n%s\nExpected:\n%s\n" %
                         (actualXML, expectedXML))

    def assert_dom_xml_equal(self, dom, expected_xml):
        xml = vmxml.format_xml(dom)
        self.assertXMLEqual(xml, expected_xml)


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
    testdir = os.path.dirname(os.path.abspath(__file__))

    conf = config.Config(stream=stream,
                         env=os.environ,
                         workingDir=testdir,
                         plugins=core.DefaultPluginManager())
    conf.plugins.addPlugin(SlowTestsPlugin())
    conf.plugins.addPlugin(StressTestsPlugin())
    conf.plugins.addPlugin(ThreadLeakPlugin())
    conf.plugins.addPlugin(ProcessLeakPlugin())
    conf.plugins.addPlugin(FileLeakPlugin())

    runner = VdsmTestRunner(stream=conf.stream,
                            verbosity=conf.verbosity,
                            config=conf)

    sys.exit(not core.run(config=conf, testRunner=runner, argv=argv))


def make_config(tunables):
    """
    Create a vdsm.config.config clone, modified by tunables
    tunables is a list of (section, key, val) tuples
    """
    cfg = configparser.ConfigParser()
    vdsm.config.set_defaults(cfg)
    for (section, key, value) in tunables:
        cfg.set(section, key, value)
    return cfg


def recorded(meth):
    """
    Method decorator recording calls to receiver __calls__ list.

    Can decorate an instance method or class method. Instance methods are
    stored in the instance __calls__ list, and class methods in the class
    __class_calls__ list.

    You are responsible for clearing the class __class_calls__.

    Note: when decorating a class method, this decorator must be after the
    @classmethod decorator:

    class Foo(objet):

        @classmethod
        @recorded
        def foo(cls):
            pass

    """
    @wraps(meth)
    def wrapper(obj, *args, **kwargs):
        if inspect.isclass(obj):
            name = "__class_calls__"
        else:
            name = "__calls__"
        try:
            recording = getattr(obj, name)
        except AttributeError:
            recording = []
            setattr(obj, name, recording)
        recording.append((meth.__name__, args, kwargs))
        return meth(obj, *args, **kwargs)
    return wrapper


class LockingThread(object):
    """
    A thread that locks the given context, for testing locks.

    When starting this thead, you should wait on its ready event, to make sure
    the thread was started.  Then you should sleep some time to make sure the
    thread tried to acquire the context.

    To check if the thread did acquire the context, wait on the the thread
    acquired event with timeout=0.

    To check that the thread is blocked on the context, wait on the acquired
    event with bigger timeout (e.g, 0.5).
    """

    def __init__(self, context):
        self.ready = threading.Event()
        self.acquired = threading.Event()
        self.done = threading.Event()
        self._context = context
        self._thread = None

    def start(self):
        self._thread = start_thread(self._run)

    def stop(self):
        self.done.set()
        self._thread.join()

    def _run(self):
        self.ready.set()
        with self._context:
            self.acquired.set()
            self.done.wait()


def start_thread(func, *args, **kwargs):
    t = threading.Thread(target=func, args=args, kwargs=kwargs)
    t.daemon = True
    t.start()
    return t


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
                _, status = osutils.uninterruptible(os.waitpid, pid, 0)
                if status != 0:
                    e = pickle.loads(os.read(r, 4006))
                    raise e
        finally:
            osutils.close_fd(r)
            osutils.close_fd(w)

    return wrapper


def online_cpus():
    return frozenset(range(os.sysconf('SC_NPROCESSORS_ONLN')))


def maybefail(meth):
    """
    Method decorator that will raise the excpetion stored in the instance's
    errors dict.

    Objects using this decorator must define an errors instance
    variable:

    class Foo(object):

        def __init__(self):
            self.errors = {}

        @maybefail
        def method_name(self):
            return True

    To make a method fail, set an error:

        obj.errors["method_name"] = ValueError

    All calls to method_name will raise now ValueError.  To stop the failures,
    delete the error:

        del obj.errors["method_name"]

    """
    @functools.wraps(meth)
    def wrapper(self, *args, **kwargs):
        try:
            exception = self.errors[meth.__name__]
        except KeyError:
            return meth(self, *args, **kwargs)
        raise exception
    return wrapper


def read_data(filename):
    """
    Returns the content of a test data file, as plain string.
    The test data file any file path under the data/ subdirectory
    in the tests directory.
    """
    caller = inspect.stack()[1]
    caller_mod = inspect.getmodule(caller[0])
    test_path = os.path.realpath(caller_mod.__file__)
    dir_name = os.path.dirname(test_path)
    path = os.path.join(dir_name, 'data', filename)
    with open(path) as src:
        return src.read()


def wait_for_job(job):
    """
    Wait for a jobs.Job to complete (either success or failure)
    """
    while job.active:
        time.sleep(1)


def make_uuid():
    """
    Return a new UUID version 4 string for use with vdsm APIs
    """
    return str(uuid.uuid4())


@cache.memoized
def ipv6_enabled():
    # Based on vdsm.network.sysctl
    path = "/proc/sys/net/ipv6/conf/default/disable_ipv6"
    try:
        with io.open(path, "rb") as f:
            value = f.read()
    except EnvironmentError as e:
        if e.errno != errno.ENOENT:
            raise
        # Kernel does not support ipv6
        return False
    # Kernel supports ipv6, but it may be disabled.
    return int(value) == 0
