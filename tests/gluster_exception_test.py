# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from testlib import VdsmTestCase
from vdsm.gluster import exception as gluster_exception
from vdsm.gluster.exception import GlusterException


class TestGlusterException(VdsmTestCase):

    def test_str_no_args(self):
        e = GlusterException()
        self.assertEqual(str(e), "Gluster Exception: rc=0 out=() err=()")

    def test_str_strings(self):
        e = GlusterException(rc=1, out='out', err='err')
        self.assertEqual(str(e), "Gluster Exception: rc=1 out='out' err='err'")

    def test_str_lists(self):
        e = GlusterException(rc=1, out=["o", "u", "t"], err=["e", "r", "r"])
        expected = ("Gluster Exception: rc=1 out=['o', 'u', 't'] "
                    "err=['e', 'r', 'r']")
        self.assertEqual(str(e), expected)

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
