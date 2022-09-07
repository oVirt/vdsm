# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from contextlib import contextmanager
import os

from monkeypatch import MonkeyPatchScope
from testlib import namedTemporaryDir, VdsmTestCase
from vdsm import config
from vdsm.common import config as common_config

parameters = [
    (
        'test',
        [
            ('key', 'default value', None),
            ('num', '0', None),
        ],
    )
]


@contextmanager
def fakedirs():
    with namedTemporaryDir() as tmpdir:
        admin_dir = os.path.join(tmpdir, 'etc')
        vendor_dir = os.path.join(tmpdir, 'usr', 'lib')
        runtime_dir = os.path.join(tmpdir, 'run')
        dirs = (admin_dir, vendor_dir, runtime_dir)
        with MonkeyPatchScope(
            [(common_config, '_SYSCONFDIR', admin_dir),
             (common_config, '_DROPPIN_BASES', dirs),
             (common_config, 'parameters', parameters),
             (config, 'parameters', parameters)]):
            yield dirs


def create_dropin(basedir, name, content):
    path = os.path.join(basedir, 'vdsm', 'vdsm.conf.d', name)
    write_file(path, content)


def create_conf(basedir, content):
    path = os.path.join(basedir, 'vdsm', 'vdsm.conf')
    write_file(path, content)


def write_file(path, content):
    dirname = os.path.dirname(path)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    with open(path, "w") as f:
        f.write(content)


class TestConfig(VdsmTestCase):
    def test_default(self):
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            cfg = config.load('vdsm')
            self.assertEqual(cfg.get('test', 'key'), 'default value')

    def test_default_conf(self):
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            create_conf(admin_dir, '[test]\nkey = default conf val\n')

            cfg = config.load('vdsm')
            self.assertEqual(cfg.get('test', 'key'), 'default conf val')

    def test_admin_dropin(self):
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            create_dropin(admin_dir, '52.conf', '[test]\nkey = admin\n')

            cfg = config.load('vdsm')
            self.assertEqual(cfg.get('test', 'key'), 'admin')

    def test_vendor_dropin(self):
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            create_dropin(vendor_dir, '53.conf', '[test]\nkey = vendor\n')

            cfg = config.load('vdsm')
            self.assertEqual(cfg.get('test', 'key'), 'vendor')

    def test_runtime_dropin(self):
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            create_dropin(admin_dir, '54.conf', '[test]\nkey = runtime\n')

            cfg = config.load('vdsm')
            self.assertEqual(cfg.get('test', 'key'), 'runtime')

    def test_dropin_override_conf(self):
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            create_conf(admin_dir, '[test]\nkey = default conf\n')
            create_dropin(
                runtime_dir, '51_runtime.conf', '[test]\nkey = runtime\n')

            cfg = config.load('vdsm')
            self.assertEqual(cfg.get('test', 'key'), 'runtime')

    def test_ignore_invalid_conf_suffix(self):
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            create_dropin(
                runtime_dir, '50_invalid_name', '[test]\nkey = runtime\n')

            cfg = config.load('vdsm')
            self.assertTrue(cfg.get('test', 'key'), 'default value')

    def test_same_directory_read_proirity(self):
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            create_conf(admin_dir, '[test]\nnum = 1\n')
            create_dropin(admin_dir, '51.conf', '[test]\nnum = 51\n')
            create_dropin(admin_dir, '52.conf', '[test]\nnum = 52\n')
            create_dropin(admin_dir, '53.conf', '[test]\nnum = 53\n')

            cfg = config.load('vdsm')
            self.assertEqual(cfg.getint('test', 'num'), 53)

    def test_different_directories_read_proirity(self):
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            create_conf(admin_dir, '[test]\nnum = 1\n')
            create_dropin(admin_dir, '51.conf', '[test]\nnum = 51\n')
            create_dropin(vendor_dir, '52.conf', '[test]\nnum = 52\n')
            create_dropin(runtime_dir, '53.conf', '[test]\nnum = 53\n')

            cfg = config.load('vdsm')
            self.assertEqual(cfg.getint('test', 'num'), 53)

    def test_options_not_modified(self):
        """
        make sure unrelated items were not modified by reading default
        values, creating new conf files, running config.load and checking
        the options still exist in ConfigParser
        """
        with fakedirs() as (admin_dir, vendor_dir, runtime_dir):
            create_dropin(
                runtime_dir, '51_runtime.conf', '[test]\nkey = runtime\n')

            cfg = config.load('vdsm')
            self.assertEqual(cfg.getint('test', 'num'), 0)
