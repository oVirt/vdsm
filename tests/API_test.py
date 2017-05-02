#
# Copyright 2016-2017 Red Hat, Inc.
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

import copy
import logging

from vdsm.common import conv
from vdsm.common import response
from vdsm.common import threadlocal
from vdsm.compat import pickle

import API

from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase
from testlib import mock
from testlib import temporaryPath
from testlib import recorded


class TestVMCreate(TestCaseBase):

    def setUp(self):
        self.uuid = API.VM.BLANK_UUID
        self.cif = FakeClientIF()
        self.vmParams = {
            'vmId': self.uuid,
            'vmName': 'TESTING',
            'memSize': 8 * 1024,
            'vmType': 'kvm',
            'display': 'qxl',
            'kvmEnable': 'true',
            'smp': '1',
        }
        with MonkeyPatchScope([(API, 'clientIF', self.cif)]):
            self.vm = API.VM(self.uuid)
        # to make testing easier
        self.vm._getHibernationPaths = lambda handle: (True, handle)
        threadlocal.vars.context = mock.Mock()

    def tearDown(self):
        threadlocal.vars.context = None

    def test_create_twice(self):
        vmParams = {
            'vmId': self.uuid,
        }
        vm = FakeVM(self.cif, vmParams)
        self.cif.vmContainer[vm.id] = vm
        try:
            res = self.vm.create({})
            self.assertTrue(response.is_error(res, 'exist'))
        finally:
            del self.cif.vmContainer[vm.id]
        self.assertEqual(self.cif.vmContainer, {})

    def test_create_without_id(self):
        res = self.vm.create({})
        self.assertTrue(response.is_error(res, 'MissParam'))

    def test_create_without_memsize(self):
        res = self.vm.create({'vmId': self.uuid})
        self.assertTrue(response.is_error(res, 'MissParam'))

    def test_create_with_invalid_id(self):
        # anything which doesn't look like an UUID
        res = self.vm.create({'vmId': 'foobar'})
        self.assertTrue(response.is_error(res, 'MissParam'))

    def test_create_with_zero_memsize(self):
        res = self.vm.create({
            'vmId': self.uuid,
            'memSize': 0,
        })
        self.assertTrue(response.is_error(res, 'MissParam'))

    def test_create_with_missing_boot_disk(self):
        res = self.vm.create({
            'vmId': self.uuid,
            'memSize': 0,
            'boot': 'c',
        })
        self.assertTrue(response.is_error(res, 'MissParam'))

    def test_create_fix_param_vmType(self):
        vmParams = {
            'vmId': self.uuid,
            'memSize': 8 * 1024,
        }
        res = self.vm.create(vmParams)
        self.assertFalse(response.is_error(res))
        self.assertEqual(vmParams.get('vmType'), 'kvm')

    def test_create_fix_param_smp(self):
        vmParams = {
            'vmId': self.uuid,
            'memSize': 8 * 1024,
        }
        res = self.vm.create(vmParams)
        self.assertFalse(response.is_error(res))
        self.assertEqual(vmParams.get('smp'), '1')

    def test_create_fix_param_vmName(self):
        vmParams = {
            'vmId': self.uuid,
            'memSize': 8 * 1024,
        }
        res = self.vm.create(vmParams)
        self.assertFalse(response.is_error(res))
        self.assertEqual(vmParams.get('vmName'), 'n%s' % self.uuid)

    def test_create_fix_param_kvmEnable(self):
        vmParams = {
            'vmId': self.uuid,
            'memSize': 8 * 1024,
            'vmType': 'kvm',
        }
        res = self.vm.create(vmParams)
        self.assertFalse(response.is_error(res))
        self.assertTrue(conv.tobool(vmParams.get('kvmEnable')))

    def test_create_unsupported_graphics(self):
        vmParams = {
            'vmId': self.uuid,
            'memSize': 8 * 1024,
            'vmType': 'kvm',
            'display': 'unsupported',
        }
        res = self.vm.create(vmParams)
        self.assertTrue(response.is_error(res, 'createErr'))

    def test_hibernation_params_requested_but_missing(self):
        vmParams = {
            'hiberVolHandle': '/this/path/does/not/exist/'
        }
        vmParams.update(self.vmParams)

        refParams = copy.deepcopy(vmParams)
        del refParams['hiberVolHandle']  # must go away
        refParams['restoreState'] = True  # to be added BY TESTS

        res = self.vm.create(vmParams)
        self.assertFalse(response.is_error(res))
        self.assertEqual(refParams, vmParams)

    def test_hibernation_params_wrong_format(self):
        vmParams = {}
        vmParams.update(self.vmParams)

        refParams = copy.deepcopy(vmParams)
        refParams['restoreState'] = True  # to be added BY TESTS

        extraParams = ['a', 42]
        with temporaryPath(data=pickle.dumps(extraParams)) as path:
            vmParams['hiberVolHandle'] = path
            res = self.vm.create(vmParams)

        res = self.vm.create(vmParams)
        self.assertFalse(response.is_error(res))
        self.assertEqual(refParams, vmParams)

    def test_hibernation_params(self):
        vmParams = {}
        vmParams.update(self.vmParams)
        extraParams = {
            'a': 42,
            'foo': ['bar'],
        }
        with temporaryPath(data=pickle.dumps(extraParams)) as path:
            vmParams['hiberVolHandle'] = path
            res = self.vm.create(vmParams)

        self.assertFalse(response.is_error(res))
        for param in extraParams:
            self.assertEqual(extraParams[param], vmParams[param])


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

    def prepareVolumePath(self, paramFilespec):
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
