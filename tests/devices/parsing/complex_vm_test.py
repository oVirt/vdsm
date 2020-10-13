from __future__ import absolute_import
from __future__ import division
import os

from vdsm.virt import domain_descriptor

from testlib import XMLTestCase
from virt import vmfakelib as fake

import verify


class TestVmDevicesXmlParsing(XMLTestCase, verify.DeviceMixin):

    def test_complex_vm(self):
        params = {
            'nicModel': 'rtl8139,pv', 'name': 'complexVm',
            'displaySecurePort': '-1', 'memSize': '256', 'displayPort': '-1',
            'display': 'qxl'}

        devices = [{'device': 'bridge', 'nicModel': 'virtio',
                    'macAddr': '52:54:00:59:F5:3F', 'type': 'interface',
                    'network': ''},
                   {'device': 'bridge', 'nicModel': 'virtio',
                    'macAddr': '52:54:00:59:FF:FF', 'type': 'interface',
                    'network': ''}]

        test_path = os.path.realpath(__file__)
        dir_name = os.path.split(test_path)[0]
        api_path = os.path.join(
            dir_name, '..', 'data', 'testComplexVm.xml')

        domain = None
        with open(api_path, 'r') as domxml:
            domain = domxml.read()

        with fake.VM(params=params, devices=devices,
                     create_device_objects=True) as vm:
            vm._domain = domain_descriptor.DomainDescriptor(domain)
            vm._getUnderlyingVmDevicesInfo()
            self.verifyDevicesConf(vm.conf['devices'])


class TestSRiovXmlParsing(XMLTestCase, verify.DeviceMixin):

    def test_sriov_vm(self):
        params = {
            'name': 'SRiovVm',
            'displaySecurePort': '-1', 'memSize': '256', 'displayPort': '-1',
            'display': 'qxl'}

        devices = [{'device': 'bridge', 'nicModel': 'virtio',
                    'macAddr': '52:54:00:59:FF:FF', 'type': 'interface',
                    'network': ''},
                   ]

        test_path = os.path.realpath(__file__)
        dir_name = os.path.split(test_path)[0]
        api_path = os.path.join(
            dir_name, '..', 'data', 'testSRiovVm.xml')

        domain = None
        with open(api_path, 'r') as domxml:
            domain = domxml.read()
        with fake.VM(params=params, devices=devices,
                     create_device_objects=True) as vm:
            vm._domain = domain_descriptor.DomainDescriptor(domain)
            vm._getUnderlyingVmDevicesInfo()
            self.verifyDevicesConf(vm.conf['devices'])
            self._assert_guest_device_adress_is_reported(vm)

    def _assert_guest_device_adress_is_reported(self, vm):
        reported = _reported_host_device(vm)
        self.assertEqual(
            reported['address'],
            {'slot': '0x07', 'bus': '0x99', 'domain': '0x0000', 'type': 'pci',
             'function': '0x0'})


def _reported_host_device(vm):
    for dev in vm.conf['devices']:
        if dev['device'] == 'hostdev':
            return dev
