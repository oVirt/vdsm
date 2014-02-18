#
# Copyright 2014 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from vdsm.tool import configurator
from vdsm import utils
import monkeypatch
from unittest import TestCase
import tempfile
import os

test_env = {}

srcPath = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

test_env['GETCONFITEM'] = os.path.join(srcPath, 'vdsm/get-conf-item')
test_env['OVIRT_FUNC_PATH'] = os.path.join(srcPath, 'vdsm/ovirt_functions.sh')
test_env['TESTING_ENV'] = 'TRUE'

sample_config = {}
sample_config['withssl'] = """
[vars]
ssl = true
"""

sample_config['withnossl'] = """
[vars]
ssl = false
"""

sample_config['empty'] = ""

sample_config['libvirt_conf'] = """
## beginning of configuration section by vdsm-4.13.0
listen_addr="0.0.0.0"
unix_sock_group="qemu"
unix_sock_rw_perms="0770"
auth_unix_rw="sasl"
host_uuid="72d18a98-8d96-4687-967a-72d989d3b65f"
keepalive_interval=-1
log_outputs="1:file:/var/log/libvirt/libvirtd.log"
ca_file="/etc/pki/vdsm/certs/cacert.pem"
cert_file="/etc/pki/vdsm/certs/vdsmcert.pem"
key_file="/etc/pki/vdsm/keys/vdsmkey.pem"
## end of configuration section by vdsm-4.13.0
"""

sample_config['qemu'] = """
## beginning of configuration section by vdsm-4.13.0
dynamic_ownership=0
spice_tls=1
save_image_format="lzop"
spice_tls_x509_cert_dir="/etc/pki/vdsm/libvirt-spice"
remote_display_port_min=5900
remote_display_port_max=6923
lock_manager="sanlock"
auto_dump_path="/var/log/core"
## end of configuration section by vdsm-4.13.0
"""

sample_config['libvirtd'] = """
## beginning of configuration section by vdsm-4.13
auto_disk_leases=0
require_lease_for_disks=0
## end of configuration section by vdsm-4.13.0
"""

sample_config['qemu-sanlock'] = """
## beginning of configuration section by vdsm-4.13.0
LIBVIRTD_ARGS=--listen
DAEMON_COREFILE_LIMIT=unlimited
## end of configuration section by vdsm-4.13.0
"""


def get_libvirt_exec(selfarg):
    return '/bin/sh',\
        os.path.realpath('../lib/vdsm/tool/libvirt_configure.sh')


class LibvirtModuleConfigureTests(TestCase):
    # currently test:
    # 1. libvirt validate configuration (validate) V
    # 2. libvirt overriding configuration on force
    # 3. isconfigured returns false when not configured and true otherwise

    # need to add
    # 1. test force=false
    # 2. sanlock configure - must run as root.
    def setUp(self):
        self._test_dir = tempfile.mkdtemp()

        test_env['LCONF'] = self._test_dir + '/libvirtd.conf'
        test_env['QCONF'] = self._test_dir + '/qemu.conf'
        test_env['LDCONF'] = self._test_dir + '/qemu-sanlock.conf'
        test_env['QLCONF'] = self._test_dir + '/libvirtd'
        test_env['LIBVIRT_LOGROTATE'] = \
            self._test_dir + '/logrotate-libvirtd'
        test_env['VDSM_CONF_FILE'] = self._test_dir + '/vdsm.conf'
        test_env['FORCE_RECONFIGURE'] = self._test_dir + '/reconfigure'

        utils.touchFile(test_env['LIBVIRT_LOGROTATE'])
        self._setConfig('QLCONF', 'libvirtd')
        self._setConfig('QCONF', 'qemu')
        self._setConfig('LDCONF', 'qemu-sanlock')
        self.patch = monkeypatch.Patch([
            (os, 'getuid', lambda: 0),
            (configurator.LibvirtModuleConfigure,
             '_get_libvirt_exec',
             get_libvirt_exec)
        ])
        self.patch.apply()

    def tearDown(self):
        self.patch.revert()
        utils.rmTree(self._test_dir)

    # helpers
    def _setConfig(self, file, type):
        with open(test_env[file], 'w') as f:
            f.write(sample_config[type])

    # ssl config validation
    def testLibvirtInvalidateSSLConfig(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure(test_env)
        self._setConfig('VDSM_CONF_FILE', 'withnossl')
        self._setConfig('LCONF', 'libvirt_conf')
        self.assertFalse(libvirtConfigure.validate())

    def testLibvirtValidSSLConfig(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure(test_env)
        self._setConfig('VDSM_CONF_FILE', 'withssl')
        self._setConfig('LCONF', 'libvirt_conf')
        self.assertTrue(libvirtConfigure.validate())

    def testLibvirtIsConfigured(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure(test_env)
        self._setConfig('VDSM_CONF_FILE', 'withssl')
        self._setConfig('LCONF', 'libvirt_conf')
        self.assertTrue(libvirtConfigure.isconfigured())

    def testLibvirtNotConfigured(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure(test_env)
        self._setConfig('LCONF', 'empty')
        self.assertFalse(libvirtConfigure.isconfigured())

    def testLibvirtConfigureToSSLTrue(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure(test_env)
        self._setConfig('LCONF', 'empty')
        self._setConfig('VDSM_CONF_FILE', 'withssl')
        self.assertFalse(libvirtConfigure.isconfigured())
        libvirtConfigure.configure()
        self.assertTrue(libvirtConfigure.isconfigured())

    def testLibvirtConfigureToSSLFalse(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure(test_env)
        self._setConfig('LCONF', 'empty')
        self._setConfig('VDSM_CONF_FILE', 'withnossl')
        self.assertFalse(libvirtConfigure.isconfigured())
        libvirtConfigure.configure()
        self.assertTrue(libvirtConfigure.isconfigured())
