#
# Copyright 2017 IBM Corp.
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

import os
from testlib import VdsmTestCase as TestCaseBase

from vdsm import machinetype
from vdsm.common import cpuarch


class FakeConnection(object):

    def __init__(self, fileName):
        testPath = os.path.realpath(__file__)
        dirName = os.path.split(testPath)[0]
        self.domcapspath = os.path.join(dirName, fileName)

    def getDomainCapabilities(self, *args):
        with open(self.domcapspath) as f:
            return f.read()


class TestDomCaps(TestCaseBase):

    def testCpuTypeS390X(self):
        conn = FakeConnection("domcaps_libvirt_s390x.out")
        dom_models = machinetype.domain_cpu_models(conn, cpuarch.S390X)
        exp_models = {'z14-base': 'yes', 'z14': 'no'}
        for model, usable in exp_models.items():
            self.assertEqual(dom_models[model], usable)
