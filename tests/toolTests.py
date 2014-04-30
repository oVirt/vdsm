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
import shutil

dirName = os.path.dirname(os.path.realpath(__file__))


class LibvirtModuleConfigureTests(TestCase):

    test_env = {}

    srcPath = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

    def setUp(self):
        self._test_dir = tempfile.mkdtemp()

        self.test_env['VDSM_CONF'] = self._test_dir + '/vdsm.conf'
        self.test_env['LCONF'] = self._test_dir + '/libvirtd.conf'
        self.test_env['QCONF'] = self._test_dir + '/qemu.conf'
        self.test_env['LDCONF'] = self._test_dir + '/qemu-sanlock.conf'
        self.test_env['QLCONF'] = self._test_dir + '/libvirtd'
        self.test_env['LRCONF'] = self._test_dir + '/logrotate-libvirtd'
        self.test_env['QNETWORK'] = 'NON_EXISTENT'
        self.test_env['LRCONF_EXAMPLE'] = os.path.join(
            LibvirtModuleConfigureTests.srcPath,
            'lib/vdsm/tool/libvirtd.logrotate'
        )

        for key, val in self.test_env.items():
            configurator.LibvirtModuleConfigure.FILES[key]['path'] = val

        self._setConfig(
            ('QLCONF', 'libvirtd'),
            ('LDCONF', 'qemu_sanlock'),
        )

        self.patch = monkeypatch.Patch([
            (
                os,
                'getuid',
                lambda: 0
            ),
            (
                configurator.LibvirtModuleConfigure,
                '_getFile',
                lambda _, x: self.test_env[x]
            ),
            (
                configurator.LibvirtModuleConfigure,
                '_sysvToUpstart',
                lambda _: True
            ),
            (
                utils,
                'isOvirtNode',
                lambda: False
            )
        ])

        self.patch.apply()

    def tearDown(self):
        self.patch.revert()
        utils.rmTree(self._test_dir)

    # helpers
    def _setConfig(self, *configurations):
        for file_, type_ in configurations:
            shutil.copyfile(
                os.path.join(dirName, 'toolTests_' + type_ + '.conf'),
                self.test_env[file_]
            )

    def testValidatePositive(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure()

        self._setConfig(
            ('VDSM_CONF', 'vdsm_ssl'),
            ('LCONF', 'lconf_ssl'),
            ('QCONF', 'qemu_ssl'),
        )

        self.assertTrue(libvirtConfigure.validate())

    def testValidateNegative(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure()

        self._setConfig(
            ('VDSM_CONF', 'vdsm_no_ssl'),
            ('LCONF', 'lconf_ssl'),
            ('QCONF', 'qemu_ssl'),
        )

        self.assertFalse(libvirtConfigure.validate())

    def testIsConfiguredPositive(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure()

        self._setConfig(
            ('LCONF', 'lconf_ssl'),
            ('QCONF', 'qemu_ssl'),
            ('LRCONF', 'libvirt_logrotate')

        )
        self.assertEqual(
            libvirtConfigure.isconfigured(),
            configurator.NOT_SURE
        )

    def testIsConfiguredNegative(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure()

        self._setConfig(
            ('LCONF', 'lconf_ssl'),
            ('QCONF', 'empty'),
            ('LRCONF', 'empty'),
        )
        self.assertEqual(
            libvirtConfigure.isconfigured(),
            configurator.NOT_CONFIGURED
        )

    def testLibvirtConfigureToSSLTrue(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure()

        self._setConfig((
            'LCONF', 'empty'),
            ('VDSM_CONF', 'vdsm_ssl'),
            ('QCONF', 'empty'),
            ('LRCONF', 'empty'),
        )

        self.assertEqual(
            libvirtConfigure.isconfigured(),
            configurator.NOT_CONFIGURED
        )

        libvirtConfigure.configure()

        self.assertEqual(
            libvirtConfigure.isconfigured(),
            configurator.NOT_SURE
        )

    def testLibvirtConfigureToSSLFalse(self):
        libvirtConfigure = configurator.LibvirtModuleConfigure()
        self._setConfig(
            ('LCONF', 'empty'),
            ('VDSM_CONF', 'vdsm_no_ssl'),
            ('QCONF', 'empty'),
            ('LRCONF', 'empty'),
        )
        self.assertEquals(
            libvirtConfigure.isconfigured(),
            configurator.NOT_CONFIGURED
        )

        libvirtConfigure.configure()

        self.assertEqual(
            libvirtConfigure.isconfigured(),
            configurator.NOT_SURE
        )


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
