# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import threading

from vdsm.common import pthread

from testlib import VdsmTestCase as TestCaseBase


class PthreadNameTests(TestCaseBase):

    def test_name_too_long(self):
        self.assertRaises(ValueError,
                          pthread.setname,
                          "any name longer than fifteen ASCII characters")

    def test_name_is_set(self):
        NAME = "test-name"
        names = [None]

        def run():
            pthread.setname(NAME)
            names[0] = pthread.getname()

        t = threading.Thread(target=run)
        t.daemon = True
        t.start()
        t.join()

        self.assertEqual(names[0], NAME)

    def test_name_set_doesnt_change_parent(self):
        HELPER_NAME = "helper-name"
        parent_name = pthread.getname()
        ready = threading.Event()
        done = threading.Event()

        def run():
            pthread.setname(HELPER_NAME)
            ready.set()
            done.wait()

        t = threading.Thread(target=run)
        t.daemon = True
        t.start()
        try:
            ready.wait()
            try:
                self.assertEqual(parent_name, pthread.getname())
            finally:
                done.set()
        finally:
            t.join()
