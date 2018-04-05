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
from vdsm.gluster import exception as gluster_exception
from vdsm.gluster.exception import GlusterException


class TestGlusterException(VdsmTestCase):

    def test_str(self):
        e = GlusterException()
        self.assertEqual(str(e), "Gluster Exception")

    def test_str_with_rc(self):
        e = GlusterException(rc=1)
        self.assertEqual(str(e), "Gluster Exception\nreturn code: 1")

    def test_str_with_out(self):
        e = GlusterException(out=["output"])
        self.assertEqual(str(e), "Gluster Exception\nerror: output")

    def test_str_with_out_multiline(self):
        e = GlusterException(out=["line 1", "line 2", "line 3"])
        self.assertEqual(str(e), "Gluster Exception\nerror: line 1\nline 2\n"
                                 "line 3")

    def test_str_with_err(self):
        e = GlusterException(err=["error"])
        self.assertEqual(str(e), "Gluster Exception\nerror: error")

    def test_str_with_err_multiline(self):
        e = GlusterException(err=["line 1", "line 2", "line 3"])
        self.assertEqual(str(e), "Gluster Exception\nerror: line 1\nline 2\n"
                                 "line 3")

    def test_str_with_out_err(self):
        e = GlusterException(out=["output"], err=["error"])
        self.assertEqual(str(e), "Gluster Exception\nerror: output\nerror")

    def test_str_with_out_err_multiline(self):
        e = GlusterException(out=["out 1", "out 2"], err=["err 1", "err 2"])
        self.assertEqual(str(e), "Gluster Exception\nerror: out 1\nout 2\n"
                                 "err 1\nerr 2")

    def test_str_with_rc_out_err(self):
        e = GlusterException(rc=1, out=["output"], err=["error"])
        self.assertEqual(str(e), "Gluster Exception\nerror: output\nerror\n"
                                 "return code: 1")

    def test_info(self):
        e = GlusterException()
        self.assertEqual(e.info(), {'code': 4100,
                                    'message': str(e),
                                    'rc': e.rc,
                                    'out': e.out,
                                    'err': e.err})

    def test_response(self):
        e = GlusterException()
        self.assertEqual(e.response(), {'status': e.info()})

    def test_collisions(self):
        codes = {}

        for name in dir(gluster_exception):
            obj = getattr(gluster_exception, name)

            if not isinstance(obj, type):
                continue

            if not issubclass(obj, GlusterException):
                continue

            self.assertFalse(obj.code in codes)
            # gluster exception range: 4100-4800
            self.assertTrue(obj.code >= 4100)
            self.assertTrue(obj.code <= 4800)
