# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
from functools import wraps
import inspect

import six


#
# Monkey patch.
#
# Usage:
# ---
# import monkeypatch
#
# class TestSomething():
#
#     def __init__(self):
#         self.patch = monkeypatch.Patch([
#             (subprocess, 'Popen', lambda x: None),
#             (os, 'chown', lambda *x: 0)
#         ])
#
#     def setUp(self):
#         self.patch.apply()
#
#     def tearDown(self):
#         self.patch.revert()
#
#     def testThis(self):
#         # using patched functions
#
#     def testThat(self):
#         # using patched functions
# ---
#
class Patch(object):

    def __init__(self, what):
        self.what = what
        self.old = []

    @staticmethod
    def _is_static_method(cls, method_name, method):
        is_static_py2 = six.PY2 and inspect.isfunction(method)
        # In Python 3, static methods are returned as 'function' and lose
        # 'staticmethod' class relationship when returned by 'getattr'
        # so we have to reach to __dict__ directly. Calling 'inspect.ismethod'
        # to differentiate between a regular method and a static method won't
        # work without referring to *bound* method and thus, creating
        # an instance of a class.
        is_static_py3 = six.PY3 and isinstance(cls.__dict__[method_name],
                                               staticmethod)
        return is_static_py2 or is_static_py3

    @staticmethod
    def _is_class_method(method):
        return (inspect.ismethod(method) and
                getattr(method, '__self__', None) is not None)

    def apply(self):
        assert self.old == []
        for module, name, that in self.what:
            old = getattr(module, name)
            self.old.append((module, name, old))
            # The following block is done so that if it is a method we are
            # patching in, that it will have the same type as the method it
            # replaced.
            if inspect.isclass(module):
                if self._is_static_method(module, name, old):
                    that = staticmethod(that)
                elif self._is_class_method(old):
                    that = classmethod(that)
            setattr(module, name, that)

    def revert(self):
        assert self.old != []
        while self.old:
            module, name, that = self.old.pop()
            # Python 2 wrongly sets the function `that' as an instancemethod
            # instead of keeping it as staticmethod.
            if inspect.isclass(module) and self._is_static_method(module,
                                                                  name, that):
                that = staticmethod(that)

            setattr(module, name, that)


#
# Monkey patch scope.
#
# Usage:
# ---
# from monkeypatch import MonkeyPatchScope
#
# def test():
#     with MonkeyPatchScope([
#         (subprocess, 'Popen', lambda x: None),
#         (os, 'chown', lambda *x: 0)
#     ])
#     logic
# ---
#
@contextmanager
def MonkeyPatchScope(what):
    patch = Patch(what)
    patch.apply()
    try:
        yield {}
    finally:
        patch.revert()


#
# Monkey patch function decorator.
#
# Usage:
# ---
# from monkeypatch import MonkeyPatch
#
# @MonkeyPatch(subprocess, 'Popen', lambda x: None)
# @MonkeyPatch(os, 'chown', lambda *x: 0)
# def test():
#     logic
# ---
#
def MonkeyPatch(module, name, that):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kw):
            with MonkeyPatchScope([(module, name, that)]):
                return f(*args, **kw)
        return wrapper
    return decorator


#
# Monkey patch class decorator.
#
# Usage:
# ---
# from monkeypatch import MonkeyClass
#
# @MonkeyClass(subprocess, 'Popen', lambda x: None)
# @MonkeyClass(os, 'chown', lambda *x: 0)
# class TestSomething():
#
#     def testThis(self):
#         # using patched functions
#
#     def testThat(self):
#         # using patched functions
# ---
#
def MonkeyClass(module, name, that):

    def setup_decorator(func):
        @wraps(func)
        def setup(self, *a, **kw):
            if not hasattr(self, '__monkeystack__'):
                self.__monkeystack__ = []
            patch = Patch([(module, name, that)])
            self.__monkeystack__.append(patch)
            patch.apply()
            return func(self, *a, **kw)
        return setup

    def teardown_decorator(func):
        @wraps(func)
        def teardown(self, *a, **kw):
            patch = self.__monkeystack__.pop()
            patch.revert()
            return func(self, *a, **kw)
        return teardown

    def wrapper(cls):
        cls.setUp = setup_decorator(cls.setUp)
        cls.tearDown = teardown_decorator(cls.tearDown)
        return cls

    return wrapper
