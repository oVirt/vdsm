# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import response
from vdsm.common.define import doneCode
from vdsm.common.define import errCode

from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase


@expandPermutations
class ResponseTests(TestCaseBase):

    def test_error(self):
        NAME = 'noVM'  # no special meaning, any error is fine
        res = response.error(NAME)

        template = errCode[NAME]
        self.assertEqual(res["status"]["code"], template["status"]["code"])
        self.assertEqual(res["status"]["message"],
                         template["status"]["message"])

    def test_error_with_message(self):
        NAME = 'noVM'  # no special meaning, any error is fine
        MESSAGE = 'we want a specific message here'
        res = response.error(NAME, MESSAGE)

        template = errCode[NAME]
        self.assertEqual(res["status"]["code"], template["status"]["code"])
        self.assertEqual(res["status"]["message"], MESSAGE)

    def test_success(self):
        res = response.success()

        self.assertEqual(res, {"status": doneCode})

    def test_success_with_message(self):
        MESSAGE = "the message was overwritten"
        res = response.success(message=MESSAGE)

        template = doneCode
        self.assertEqual(res["status"]["code"], template["code"])
        self.assertEqual(res["status"]["message"], MESSAGE)

    def test_success_with_args(self):
        res = response.success(a=1, b=2)

        self.assertEqual(res, {"status": doneCode, "a": 1, "b": 2})

    @permutations([[{'answer': 42}], [{'fooList': ['bar', 'baz']}]])
    def test_success_with_extra_args(self, args):
        res = response.success(**args)
        self.assertEqual(res['status']['code'], 0)
        self.assertEqual(res['status']['message'], 'Done')
        for key in args:
            self.assertEqual(res[key], args[key])

    def test_is_error(self):
        NAME = 'noVM'  # no special meaning, any error is fine
        self.assertTrue(response.is_error(response.error(NAME)))

    @permutations((
        ('noVM', 'noVM'),
        ('hookError', 'hookError'),
        ('noVM', 'hookError')
    ))
    def test_is_specific_error(self, actual_err, expected_err):
        match = actual_err == expected_err
        self.assertEqual(match, response.is_error(response.error(actual_err),
                                                  err=expected_err))

    def test_malformed_empty(self):
        self.assertRaises(response.MalformedResponse,
                          response.is_error,
                          {})

    def test_malformed_missing_code(self):
        self.assertRaises(response.MalformedResponse,
                          response.is_error,
                          {'status': {}})

    @permutations([
        # res
        [response.success()],
        [response.success(foo='bar', a=42)],
        [response.error('noVM')],
        [{'status': {'code': '0', 'message': 'ok', 'foo': 'bar'}}],
    ])
    def test_is_valid(self, res):
        self.assertTrue(response.is_valid(res))

    @permutations([
        # res
        [('foo', 'bar')],
        [['foo', 'bar']],
        [{'code': 42}],
        [{'message': 'foobar'}],
        [{'success': True}],
    ])
    def test_is_not_valid(self, res):
        self.assertFalse(response.is_valid(res))

    def test_malformed_exception_contains_response(self):
        bad_res = {}
        try:
            response.is_error(bad_res)
        except response.MalformedResponse as ex:
            self.assertEqual(ex.response, bad_res)

    def test_malformed_exception_str(self):
        bad_res = {}
        try:
            response.is_error(bad_res)
        except response.MalformedResponse as ex:
            self.assertEqual(str(ex),
                             "Missing required key in {}")

    # TODO: drop this once we get rid of errCode
    def test_legacy_error_code(self):
        for code, res in errCode.items():
            self.assertTrue(response.is_error(res))
            self.assertEqual(res, response.error(code))
