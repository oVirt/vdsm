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
from vdsm.tool.configfile import ConfigFile, ParserWrapper
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


class ConfigFileTests(TestCase):
    def setUp(self):
        fd, self.tname = tempfile.mkstemp()
        os.close(fd)

    def tearDown(self):
        os.remove(self.tname)

    # helper function
    def _writeConf(self, text):
        with open(self.tname, 'w') as f:
            f.write(text)

    def testAddExistingConf(self):
        self._writeConf("key1=val1\n"
                        "    key2    =val2\n"
                        "#key3=val4")
        with ConfigFile(self.tname,
                        version='3.4.4',
                        sectionStart="# start conf",
                        sectionEnd="# end conf") as conf:
            conf.addEntry("key3", "val3")
            conf.addEntry("key2", "val3")

        with open(self.tname, 'r') as f:
            self.assertEqual(f.read(), "key1=val1\n"
                                       "    key2    =val2\n"
                                       "#key3=val4"
                                       "# start conf-3.4.4\n"
                                       "key3=val3\n"
                                       "# end conf-3.4.4\n")

    def testPrefixAndPrepend(self):
        self._writeConf("/var/log/libvirt/libvirtd.log {\n"
                        "        weekly\n"
                        "}\n")
        with ConfigFile(self.tname,
                        version='3.4.4',
                        sectionStart="# start conf",
                        sectionEnd="# end conf",
                        prefix="# comment ") as conf:
                    conf.prefixLines()
                    conf.prependSection("Some text to\n"
                                        "add at the top\n")
        with open(self.tname, 'r') as f:
            self.assertEqual(f.read(),
                             "# start conf-3.4.4\n"
                             "Some text to\n"
                             "add at the top\n"
                             "# end conf-3.4.4\n"
                             "# comment /var/log/libvirt/libvirtd.log {\n"
                             "# comment         weekly\n"
                             "# comment }\n")

    def testPrefixIdempotencey(self):
        original = (
            "/var/log/libvirt/libvirtd.log {\n"
            "        weekly\n"
            "}\n"
        )
        self._writeConf(original)
        with ConfigFile(self.tname,
                        version='3.4.4',
                        sectionStart="# start conf",
                        sectionEnd="# end conf",
                        prefix="# comment ") as conf:
                    conf.prefixLines()
        with open(self.tname, 'r') as f:
            self.assertEqual(f.read(),
                             "# comment /var/log/libvirt/libvirtd.log {\n"
                             "# comment         weekly\n"
                             "# comment }\n")
        with ConfigFile(self.tname,
                        version='3.4.4',
                        sectionStart="# start conf",
                        sectionEnd="# end conf",
                        prefix="# comment ") as conff:
            conff.unprefixLines()
        with open(self.tname, 'r') as f:
            self.assertEqual(f.read(), original)

    def testRemoveEntireLinePrefix(self):
        self._writeConf("# comment\n")
        with ConfigFile(self.tname,
                        version='3.4.4',
                        sectionStart="# start conf",
                        sectionEnd="# end conf",
                        prefix="# comment") as conf:
            conf.unprefixLines()
        with open(self.tname, 'r') as f:
            self.assertEqual(f.read(), "\n")

    def testRemoveConfSection(self):
        self._writeConf("key=val\n"
                        "remove me!(see 'Backward compatibility')# by vdsm\n"
                        "key=val\n"
                        "# start conf-text-here don't matter\n"
                        "all you sections are belong to us\n"
                        "# end conf-text-here don't matter\n"
                        "# comment line\n")
        with ConfigFile(self.tname,
                        version='3.4.4',
                        sectionStart="# start conf",
                        sectionEnd="# end conf",
                        prefix="# comment") as conf:
                            conf.removeConf()
        with open(self.tname, 'r') as f:
                    self.assertEqual(f.read(), "key=val\n"
                                               "key=val\n"
                                               "# comment line\n")

    def testOutOfContext(self):
        conff = ConfigFile(self.tname,
                           version='3.4.4',
                           sectionStart="# start conf",
                           sectionEnd="# end conf")
        self.assertRaises(RuntimeError, conff.prefixLines)
        self.assertRaises(RuntimeError, conff.removeConf)

    def testHasConf(self):
        self._writeConf("key=val\n"
                        "kay=val\n"
                        "# start conf-3.4.4\n"
                        "all you sections are belong to us\n"
                        "# end conf-3.4.4\n")
        self.assertTrue(ConfigFile(self.tname,
                                   version='3.4.4',
                                   sectionStart="# start conf",
                                   sectionEnd="# end conf").hasConf())

    def testConfRead(self):
        self._writeConf("key=val\n"
                        "key1=val1\n")
        conff = ParserWrapper(None)
        conff.read(self.tname)
        self.assertEqual(conff.get('key'), 'val')

    def testConfDefaults(self):
        self._writeConf("key=val\n"
                        "key1=val1\n")
        conff = ParserWrapper({'key2': 'val2'})
        conff.read(self.tname)
        self.assertEqual(conff.get('key2'), 'val2')
