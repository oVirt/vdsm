# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import api
from vdsm.common import concurrent
from vdsm.common import exception
from vdsm.common import response
from vdsm.common.threadlocal import vars

from testlib import Sigargs
from testlib import VdsmTestCase as TestCaseBase


class TestApiMethod(TestCaseBase):

    def test_preserve_signature(self):
        vm = FakeVM()
        args = Sigargs(vm.fail)
        self.assertEqual(args.args, ['self', 'exc'])
        self.assertEqual(args.varargs, None)
        self.assertEqual(args.keywords, None)


class TestResponse(TestCaseBase):

    def setUp(self):
        self.vm = FakeVM()

    def test_success_without_return(self):
        res = self.vm.succeed()
        self.assertEqual(res, response.success())

    def test_success_with_return_dict(self):
        vmList = ['foobar']
        res = self.vm.succeed_with_return({'vmList': vmList})
        self.assertEqual(response.is_error(res), False)
        self.assertEqual(res['vmList'], vmList)

    def test_success_with_args(self):
        args = ("foo", "bar")
        res = self.vm.succeed_with_args(*args)
        self.assertEqual(response.is_error(res), False)
        self.assertEqual(res['args'], args)

    def test_success_with_kwargs(self):
        kwargs = {"foo": "bar"}
        res = self.vm.succeed_with_kwargs(**kwargs)
        self.assertEqual(res['kwargs'], kwargs)
        self.assertEqual(response.is_error(res), False)

    def test_success_with_wrong_return(self):
        vmList = ['foobar']  # wrong type as per @api.method contract
        self.assertRaises(TypeError,
                          self.vm.succeed_with_return,
                          vmList)

    def test_success_with_return_dict_override_message(self):
        message = 'this message overrides the default'
        res = self.vm.succeed_with_return({'message': message})
        self.assertEqual(response.is_error(res), False)
        self.assertEqual(res['status']['message'], message)

    def test_fail_with_vdsm_exception(self):
        exc = exception.NoSuchVM()
        res = self.vm.fail(exc)
        expected = exception.NoSuchVM().response()
        self.assertEqual(res, expected)

    def test_fail_with_general_exception(self):
        exc = ValueError()
        res = self.vm.fail(exc)
        expected = exception.GeneralException(str(exc)).response()
        self.assertEqual(res, expected)

    def test_passthrough(self):
        foo = 'foo'
        res = self.vm.succeed_passthrough(foo=foo)
        self.assertEqual(res, response.success(foo=foo))


class FakeVM(object):

    @api.method
    def fail(self, exc):
        raise exc

    @api.method
    def succeed(self):
        pass

    @api.method
    def succeed_with_return(self, ret):
        return ret

    @api.method
    def succeed_with_args(self, *args):
        return {"args": args}

    @api.method
    def succeed_with_kwargs(self, **kwargs):
        return {"kwargs": kwargs}

    @api.method
    def succeed_passthrough(self, foo):
        return response.success(foo=foo)


class TestLoggedWithContext(TestCaseBase):

    def test_success(self):
        # TODO: test logged message
        context = api.Context("flow_id", "1.2.3.4", 5678)
        result = run_with_vars(context, None, Logged().succeed, "a", b=1)
        self.assertEqual(result, (("a",), {"b": 1}))

    def test_fail(self):
        # TODO: test logged message
        context = api.Context("flow_id", "1.2.3.4", 5678)
        error = RuntimeError("Expected failure")
        with self.assertRaises(RuntimeError) as e:
            run_with_vars(context, None, Logged().fail, error)
        self.assertIs(e.exception, error)


class TestLoggedWithoutContext(TestCaseBase):

    def test_success(self):
        # TODO: test logged message
        result = run_with_vars(None, None, Logged().succeed, "a", b=1)
        self.assertEqual(result, (("a",), {"b": 1}))

    def test_fail(self):
        # TODO: test logged message
        error = RuntimeError("Expected failure")
        with self.assertRaises(RuntimeError) as e:
            run_with_vars(None, None, Logged().fail, error)
        self.assertIs(e.exception, error)


class TestLoggedWithTask(TestCaseBase):

    def test_success(self):
        # TODO: test logged message
        context = api.Context("flow_id", "1.2.3.4", 5678)
        task = Task("task_id")
        result = run_with_vars(context, task, Logged().succeed, "a", b=1)
        self.assertEqual(result, (("a",), {"b": 1}))

    def test_fail(self):
        # TODO: test logged message
        context = api.Context("flow_id", "1.2.3.4", 5678)
        task = Task("task_id")
        error = RuntimeError("Expected failure")
        with self.assertRaises(RuntimeError) as e:
            run_with_vars(context, task, Logged().fail, error)
        self.assertIs(e.exception, error)


def run_with_vars(context, task, func, *args, **kwargs):
    """
    Run func in another thread with optional context and task set in the vars
    thread local.

    Return the function result or raises the original exceptions raised by
    func.
    """
    result = [None]

    def run():
        if context:
            vars.context = context
        if task:
            vars.task = task
        try:
            result[0] = (True, func(*args, **kwargs))
        except Exception as exc:
            result[0] = (False, exc)

    t = concurrent.thread(run)
    t.start()
    t.join()

    ok, value = result[0]
    if not ok:
        raise value
    return value


class Task(object):

    def __init__(self, id):
        self.id = id


class Logged(object):

    @api.logged("test")
    def succeed(self, *args, **kwargs):
        return args, kwargs

    @api.logged("test")
    def fail(self, exc):
        raise exc
