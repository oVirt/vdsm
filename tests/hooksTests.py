import os.path
#
# Copyright 2012 Red Hat, Inc.
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

import tempfile
import os
from testrunner import VdsmTestCase as TestCaseBase

import hooks


class TestHooks(TestCaseBase):
    def test_emptyDir(self):
        dirName = tempfile.mkdtemp()
        DOMXML = "algo"
        self.assertEqual(DOMXML, hooks._runHooksDir(DOMXML, dirName))
        os.rmdir(dirName)

    def createTempScripts(self):
        dirName = tempfile.mkdtemp()
        Q = 3
        code = """#! /bin/bash
echo -n %s >> "$_hook_domxml"
        """
        scripts = [tempfile.NamedTemporaryFile(dir=dirName, delete=False)
                   for n in xrange(Q)]
        scripts.sort(key=lambda f: f.name)
        for n, script in enumerate(scripts):
            script.write(code % n)
            os.chmod(os.path.join(dirName, script.name), 0775)
            script.close()
        return dirName, scripts

    def destroyTempScripts(self, scripts, dirName):
        for script in scripts:
            os.unlink(script.name)
        os.rmdir(dirName)

    def test_scriptsPerDir(self):
        dirName, scripts = self.createTempScripts()
        sNames = [script.name for script in scripts]
        hooksNames = hooks._scriptsPerDir(dirName)
        hooksNames.sort()
        self.assertEqual(sNames, hooksNames)
        self.destroyTempScripts(scripts, dirName)

    def test_runHooksDir(self):
        dirName, scripts = self.createTempScripts()
        Q = 3
        DOMXML = "algo"
        expectedResult = DOMXML
        for n in xrange(Q):
            expectedResult = expectedResult + str(n)
        res = hooks._runHooksDir(DOMXML, dirName)
        self.assertEqual(expectedResult, res)
        self.destroyTempScripts(scripts, dirName)

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
        os.chmod(script.name, 0775)
        return script.name, '683394fc34f6830dd1882418eefd9b66'

    def test_getScriptInfo(self):
        sName, md5 = self.createScript()
        info = hooks._getScriptInfo(sName)
        os.unlink(sName)
        self.assertEqual({'md5': md5}, info)

    def test_getHookInfo(self):
        dir = tempfile.mkdtemp()
        sName, md5 = self.createScript(dir)
        NEscript = tempfile.NamedTemporaryFile(dir=dir)
        os.chmod(NEscript.name, 0000)
        info = hooks._getHookInfo(dir)
        os.unlink(sName)
        expectedRes = dict([(os.path.basename(sName), {'md5': md5})])
        self.assertEqual(expectedRes, info)

    def test_deviceCustomProperties(self):
        dirName = tempfile.mkdtemp()
        script = tempfile.NamedTemporaryFile(dir=dirName, delete=False)
        code = """#!/usr/bin/python

import os
import hooking

domXMLFile = file(os.environ['_hook_domxml'], 'a')
customProperty = os.environ['customProperty']
domXMLFile.write(customProperty)
        """

        script.write(code)
        os.chmod(script.name, 0775)
        script.close()

        result = hooks._runHooksDir("oVirt", dirName,
                                    params={'customProperty': ' rocks!'})
        self.assertEqual(result, "oVirt rocks!")
