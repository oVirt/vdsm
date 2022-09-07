# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from testlib import VdsmTestCase
from vdsm.common.exception import (
    ActionStopped,
    ContextException,
    GeneralException,
    VdsmException,
)


class TestVdsmException(VdsmTestCase):

    def test_str(self):
        e = VdsmException()
        self.assertEqual(str(e), e.msg)

    def test_info(self):
        e = VdsmException()
        self.assertEqual(e.info(), {"code": 0, "message": str(e)})

    def test_response(self):
        e = VdsmException()
        self.assertEqual(e.response(), {"status": e.info()})


class TestContextException(VdsmTestCase):

    def test_context_no_arguments(self):
        e = ContextException()
        self.assertEqual(e.context, {})

    def test_context_single_argument(self):
        e = ContextException("not hot enough")
        self.assertEqual(e.context, dict(reason="not hot enough"))

    def test_context_explicit_reason(self):
        e = ContextException(reason="not hot enough")
        self.assertEqual(e.context, dict(reason="not hot enough"))

    def test_context_reason_and_kwargs(self):
        e = ContextException("not hot enough", temperature=42)
        self.assertEqual(e.context,
                         dict(reason="not hot enough", temperature=42))

    def test_str(self):
        e = ContextException("not hot enough", temperature=42)
        self.assertEqual(str(e), "%s: %s" % (e.msg, e.context))

    def test_info(self):
        e = ContextException("not hot enough", temperature=42)
        self.assertEqual(e.info(), {"code": 0, "message": str(e)})

    def test_response(self):
        e = ContextException("not hot enough", temperature=42)
        self.assertEqual(e.response(), {"status": e.info()})


class TestGeneralException(VdsmTestCase):

    def test_str(self):
        e = GeneralException()
        self.assertEqual(str(e), "General Exception: ()")

    def test_str_with_args(self):
        e = GeneralException("foo", "bar")
        self.assertEqual(str(e), "General Exception: ('foo', 'bar')")

    def test_info(self):
        e = GeneralException()
        self.assertEqual(e.info(), {"code": 100, "message": str(e)})

    def test_response(self):
        e = GeneralException()
        self.assertEqual(e.response(), {"status": e.info()})


class TestActionStopped(VdsmTestCase):

    def test_str(self):
        e = ActionStopped()
        self.assertEqual(str(e), "Action was stopped: ()")
