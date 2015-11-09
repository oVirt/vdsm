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
from testlib import namedTemporaryDir
import os
from testlib import VdsmTestCase, make_config

from vdsm import config


ADMIN_PATH = "etc/"
CONF_PATH = "vdsm/vdsm.conf.d"

RUNTIME_PATH = "run/"

VENDOR_PATH = "usr/lib/"

SSL_TRUE = "[vars]\nssl = true\n"
SSL_FALSE = "[vars]\nssl = false\n"


def create_conf(path, file_name, content):
    file_path = os.path.join(path, CONF_PATH)
    os.makedirs(file_path)
    abs_file_name = os.path.join(file_path, file_name)
    with open(abs_file_name, 'w') as f:
        f.write(content)


class TestConfig(VdsmTestCase):

    def test_conf_priority(self):
        cfg = make_config([('vars', 'ssl', 'true')])
        with namedTemporaryDir() as path:
            admin_path = os.path.join(path, ADMIN_PATH)
            create_conf(admin_path, "50_admin.conf", SSL_TRUE)
            runtime_path = os.path.join(path, RUNTIME_PATH)
            create_conf(runtime_path, "51_runtime.conf", SSL_FALSE)
            vendor_path = os.path.join(path, VENDOR_PATH)
            create_conf(vendor_path, "52_vendor.conf", SSL_TRUE)
            config.read_configs(cfg, "vdsm", [admin_path, runtime_path])
            self.assertFalse(cfg.getboolean("vars", "ssl"))
            config.read_configs(
                cfg, "vdsm", [admin_path, runtime_path, vendor_path])
        self.assertTrue(cfg.getboolean("vars", "ssl"))

    def test_conf_override(self):
        cfg = make_config([('vars', 'ssl', 'true')])
        with namedTemporaryDir() as path:
            runtime_path = os.path.join(path, RUNTIME_PATH)
            create_conf(runtime_path, "51_runtime.conf", SSL_FALSE)
            config.read_configs(cfg, "vdsm", [runtime_path])
        self.assertFalse(cfg.getboolean("vars", "ssl"))

    def test_vars_not_deleted(self):
        cfg = make_config([('vars', 'ssl', 'true')])
        with namedTemporaryDir() as path:
            runtime_path = os.path.join(path, RUNTIME_PATH)
            create_conf(runtime_path, "51_runtime.conf", SSL_FALSE)
            config.read_configs(cfg, "vdsm", [runtime_path])
        self.assertTrue(cfg.has_option('addresses', 'management_port'))
        self.assertEqual(cfg.get("addresses", "management_ip"), '::')
