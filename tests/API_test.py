#
# Copyright 2016-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import copy
import logging

from vdsm import API
from vdsm.common import api
from vdsm.common import response
from vdsm.common import threadlocal

from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase
from testlib import recorded
from virt import vmfakelib


class TestVMCreate(TestCaseBase):
    # An old hibernation volume (<4.2) format, consists of 6 UUIDs:
    # storage domain ID, storage pool ID,
    # memory dump image ID, memory dump volume ID,
    # memory conf image ID, memory conf volume ID
    _hibernation_volume_old_format = "0,1,2,3,4,5"

    def setUp(self):
        self.uuid = API.VM.BLANK_UUID
        self.cif = FakeClientIF()
        self.domain_xml = vmfakelib.default_domain_xml(vm_id=self.uuid)
        self.vmParams = {
            'vmId': self.uuid,
            'vmName': 'TESTING',
            'memSize': 8 * 1024,
            'vmType': 'kvm',
            'display': 'qxl',
            'kvmEnable': 'true',
            'smp': '1',
            'xml': self.domain_xml,
        }
        with MonkeyPatchScope([(API, 'clientIF', self.cif)]):
            self.vm = API.VM(None)
        # to make testing easier
        threadlocal.vars.context = api.Context("flow_id", "1.2.3.4", 5678)

    def tearDown(self):
        threadlocal.vars.context = None

    def test_create_twice(self):
        vmParams = {
            'vmId': None,
            'xml': self.domain_xml,
        }
        vm = FakeVM(self.cif, vmParams)
        self.cif.vmContainer[self.uuid] = vm
        try:
            res = self.vm.create(vmParams)
            self.assertTrue(response.is_error(res, 'exist'))
        finally:
            del self.cif.vmContainer[self.uuid]
        self.assertEqual(self.cif.vmContainer, {})

    def test_create_unsupported_graphics(self):
        vmParams = {
            'vmId': self.uuid,
            'memSize': 8 * 1024,
            'vmType': 'kvm',
            'display': 'unsupported',
            'xml': self.domain_xml,
        }
        res = self.vm.create(vmParams)
        self.assertTrue(response.is_error(res, 'createErr'))

    def test_hibernation_params_map_memory_dump(self):
        vmParams = {'hiberVolHandle': self._hibernation_volume_old_format}
        vmParams.update(self.vmParams)

        res = self.vm.create(vmParams)
        self.assertFalse(response.is_error(res))
        expected_memory_dump = {'device': 'disk', 'domainID': '0',
                                'poolID': '1', 'imageID': '2', 'volumeID': '3'}
        self.assertEqual(expected_memory_dump, vmParams['restoreState'])

    def test_get_hibernation_paths_from_old_params(self):
        vmParams = {
            'hiberVolHandle': self._hibernation_volume_old_format
        }
        vmParams.update(self.vmParams)

        memory_dump, memory_conf = self.vm._getHibernationPaths(vmParams)
        expected_memory_dump = {'device': 'disk', 'domainID': '0',
                                'poolID': '1', 'imageID': '2', 'volumeID': '3'}
        expected_memory_conf = {'device': 'disk', 'domainID': '0',
                                'poolID': '1', 'imageID': '4', 'volumeID': '5'}
        self.assertEqual(expected_memory_dump, memory_dump)
        self.assertEqual(expected_memory_conf, memory_conf)

    def test_get_hibernation_paths_from_new_params(self):
        vmParams = {
            'memoryDumpVolume': {'domainID': '0', 'poolID': '1',
                                 'imageID': '2', 'volumeID': '3'},
            'memoryConfVolume': {'domainID': '0', 'poolID': '1',
                                 'imageID': '2', 'volumeID': '3'}
        }
        vmParams.update(self.vmParams)

        expected_memory_dump = copy.deepcopy(vmParams['memoryDumpVolume'])
        expected_memory_dump['device'] = 'disk'
        expected_memory_conf = copy.deepcopy(vmParams['memoryConfVolume'])
        expected_memory_conf['device'] = 'disk'

        memory_dump, memory_conf = self.vm._getHibernationPaths(vmParams)
        self.assertEqual(expected_memory_dump, memory_dump)
        self.assertEqual(expected_memory_conf, memory_conf)


class FakeClientIF(object):

    def __init__(self):
        self.vmContainer = {}
        self.irs = None
        self.log = logging.getLogger('tests.FakeClientIF')

    @recorded
    def createVm(self, vmParams):
        return response.success(vmList=[self])

    def getInstance(self):
        return self

    def prepareVolumePath(self, paramFilespec, path=None):
        return paramFilespec

    def teardownVolumePath(self, paramFilespec):
        pass


class FakeVM(object):

    def __init__(self, cif, params, recover=False):
        self.recovering = recover
        self.conf = {'_blockJobs': {}, 'clientIp': ''}
        self.conf.update(params)
        self.cif = cif
        self.log = logging.getLogger('tests.FakeVM')
        self.id = self.conf['vmId']
