# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import os

from testlib import VdsmTestCase as TestCaseBase

from vdsm.common import fileutils


class ParseKeyValFileTest(TestCaseBase):

    src_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

    def test_parse_kvs(self):
        path_to_fake_conf = os.path.join(self.src_path, 'common',
                                         'fileutils_tests_qemu.conf')
        kvs = fileutils.parse_key_val_file(path_to_fake_conf)
        self.assertTrue('vnc_tls' in kvs)
        self.assertEqual('1', kvs.get('vnc_tls'))

    def test_ignore_commented(self):
        path_to_fake_conf = os.path.join(self.src_path, 'common',
                                         'fileutils_tests_qemu.conf')
        kvs = fileutils.parse_key_val_file(path_to_fake_conf)
        self.assertFalse('vnc_commented' in kvs)
