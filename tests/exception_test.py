#
# Copyright 2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
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
        self.assertEqual(str(e), e.message)

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
        self.assertEqual(str(e), "%s: %s" % (e.message, e.context))

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
