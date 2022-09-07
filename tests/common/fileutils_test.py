# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os

from testlib import VdsmTestCase as TestCaseBase, namedTemporaryDir

from vdsm.common import fileutils


class TestException(Exception):
    pass


class AtomicFileWriteTest(TestCaseBase):

    def test_exception(self):
        TEXT = 'foo'
        with namedTemporaryDir() as tmp_dir:
            test_file_path = os.path.join(tmp_dir, 'foo.txt')
            with self.assertRaises(TestException):
                with fileutils.atomic_file_write(test_file_path, 'w') as f:
                    f.write(TEXT)
                    raise TestException()
            self.assertFalse(os.path.exists(test_file_path))
            # temporary file was removed
            self.assertEqual(len(os.listdir(tmp_dir)), 0)

    def test_create_a_new_file(self):
        TEXT = 'foo'
        with namedTemporaryDir() as tmp_dir:
            test_file_path = os.path.join(tmp_dir, 'foo.txt')
            with fileutils.atomic_file_write(test_file_path, 'w') as f:
                f.write(TEXT)
                self.assertFalse(os.path.exists(test_file_path))
            self._assert_file_contains(test_file_path, TEXT)
            # temporary file was removed
            self.assertEqual(len(os.listdir(tmp_dir)), 1)

    def test_edit_file(self):
        OLD_TEXT = 'foo'
        NEW_TEXT = 'bar'
        with namedTemporaryDir() as tmp_dir:
            test_file_path = os.path.join(tmp_dir, 'foo.txt')
            with open(test_file_path, 'w') as f:
                f.write(OLD_TEXT)
            with fileutils.atomic_file_write(test_file_path, 'w') as f:
                f.write(NEW_TEXT)
                self._assert_file_contains(test_file_path, OLD_TEXT)
            self._assert_file_contains(test_file_path, NEW_TEXT)
            # temporary file was removed
            self.assertEqual(len(os.listdir(tmp_dir)), 1)

    def _assert_file_contains(self, path, expected_content):
        with open(path) as f:
            content = f.read()
            self.assertEqual(content, expected_content)


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
