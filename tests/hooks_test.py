# encoding: utf-8
#
# Copyright 2012-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import
from __future__ import division


import contextlib
import libvirt
import tempfile
import os
import os.path
from contextlib import contextmanager
from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase
from testlib import namedTemporaryDir

from vdsm.common import hooks


class TestHooks(TestCaseBase):
    def test_emptyDir(self):
        with namedTemporaryDir() as dirName:
            DOMXML = "algo"
            self.assertEqual(DOMXML, hooks._runHooksDir(DOMXML, dirName))

    @contextlib.contextmanager
    def tempScripts(self):
        with namedTemporaryDir() as dirName:
            Q = 3
            code = """#! /bin/bash
echo -n %s >> "$_hook_domxml"
            """
            scripts = [tempfile.NamedTemporaryFile(dir=dirName, delete=False)
                       for n in range(Q)]
            scripts.sort(key=lambda f: f.name)
            for n, script in enumerate(scripts):
                script.write(code % n)
                os.chmod(os.path.join(dirName, script.name), 0o775)
                script.close()
            yield dirName, scripts

    def test_scriptsPerDir(self):
        with self.tempScripts() as (dirName, scripts):
            sNames = [script.name for script in scripts]
            hooksNames = hooks._scriptsPerDir(dirName)
            hooksNames.sort()
            self.assertEqual(sNames, hooksNames)

    def test_runHooksDir(self):
        # Add an unicode value to the environment variables
        # to test whether the utf-8 recoding works properly
        os.environ["FAKE_GERRIT_USERNAME"] = "Pěkný žluťoučký kůň"

        with self.tempScripts() as (dirName, scripts):
            Q = 3
            DOMXML = "algo"
            expectedResult = DOMXML
            for n in range(Q):
                expectedResult = expectedResult + str(n)
            res = hooks._runHooksDir(DOMXML, dirName)
            self.assertEqual(expectedResult, res)

    def test_getNEScriptInfo(self):
        path = '/tmp/nonExistent'
        info = hooks._getScriptInfo(path)
        self.assertEqual({'md5': ''}, info)

    def createScript(self, dir='/tmp'):
        script = tempfile.NamedTemporaryFile(dir=dir, delete=False)
        code = """#! /bin/bash
echo "81212590184644762"
        """
        script.write(code)
        script.close()
        os.chmod(script.name, 0o775)
        return script.name, '683394fc34f6830dd1882418eefd9b66'

    def test_getScriptInfo(self):
        sName, md5 = self.createScript()
        info = hooks._getScriptInfo(sName)
        os.unlink(sName)
        self.assertEqual({'md5': md5}, info)

    def test_getHookInfo(self):
        with namedTemporaryDir() as dir:
            sName, md5 = self.createScript(dir)
            with tempfile.NamedTemporaryFile(dir=dir) as NEscript:
                os.chmod(NEscript.name, 0o000)
                info = hooks._getHookInfo(dir)
                expectedRes = dict([(os.path.basename(sName), {'md5': md5})])
                self.assertEqual(expectedRes, info)

    @contextmanager
    def _deviceCustomPropertiesTestFile(self):
        with namedTemporaryDir() as dirName:
            # two nested with blocks to be python 2.6 friendly
            with tempfile.NamedTemporaryFile(dir=dirName, delete=False) as f:
                code = """#!/usr/bin/python2

import os
import hooking

domXMLFile = open(os.environ['_hook_domxml'], 'a')
customProperty = os.environ['customProperty']
domXMLFile.write(customProperty)
domXMLFile.close()
            """
                f.write(code)
                os.chmod(f.name, 0o775)
            yield dirName

    def test_deviceCustomProperties(self):
        with self._deviceCustomPropertiesTestFile() as dirName:
            result = hooks._runHooksDir("oVirt", dirName,
                                        params={'customProperty': ' rocks!'})
            self.assertEqual(result, "oVirt rocks!")

    def test_deviceVmConfProperties(self):
        with self._deviceCustomPropertiesTestFile() as dirName:
            vmconf = {
                'custom': {
                    'customProperty': ' rocks more!'}}

            result = hooks._runHooksDir("oVirt", dirName,
                                        params={'customProperty': ' rocks!'},
                                        vmconf=vmconf)
            self.assertEqual(result, "oVirt rocks more!")

    def test_pause_flags(self):
        vm_id = '042f6258-3446-4437-8034-0c93e3bcda1b'
        with namedTemporaryDir() as tmpDir:
            flags_path = os.path.join(tmpDir, '%s')
            with MonkeyPatchScope([(hooks, '_LAUNCH_FLAGS_PATH', flags_path)]):
                flags_file = hooks._LAUNCH_FLAGS_PATH % vm_id
                for flag in [libvirt.VIR_DOMAIN_NONE,
                             libvirt.VIR_DOMAIN_START_PAUSED]:
                    self.assertFalse(os.path.exists(flags_file))
                    hooks.dump_vm_launch_flags_to_file(vm_id, flag)
                    read_flag = hooks.load_vm_launch_flags_from_file(vm_id)
                    self.assertEqual(flag, read_flag)
                    self.assertTrue(os.path.exists(flags_file))
                    hooks.remove_vm_launch_flags_file(vm_id)
                    self.assertFalse(os.path.exists(flags_file))
