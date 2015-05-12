import os

from testlib import XMLTestCase

from virt import domain_descriptor
import vmfakelib as fake

import verify


class TestVmDevicesXmlParsing(XMLTestCase, verify.DeviceMixin):

    def test_complex_vm(self):
        params = {
            'nicModel': 'rtl8139,pv', 'name': 'complexVm',
            'displaySecurePort': '-1', 'memSize': '256', 'displayPort': '-1',
            'display': 'qxl'}

        devices = [{'device': 'ac97', 'type': 'sound'},
                   {'device': 'ich6', 'type': 'sound'},
                   {'device': 'qxl', 'type': 'video'},
                   {'device': 'qxl', 'type': 'video'},
                   {'device': 'spice', 'type': 'graphics'},
                   {'device': 'virtio-serial', 'type': 'controller'},
                   {'device': 'usb', 'type': 'controller'},
                   {'device': 'memballoon', 'specParams': {'model': 'virtio'},
                    'type': 'balloon'},
                   {'device': 'watchdog', 'type': 'watchdog'},
                   {'device': 'smartcard', 'specParams':
                    {'type': 'spicevmc', 'mode': 'passthrough'},
                    'type': 'smartcard'},
                   {'device': 'console', 'type': 'console'},
                   {'device': 'bridge', 'nicModel': 'virtio',
                    'macAddr': '52:54:00:59:F5:3F', 'type': 'interface',
                    'network': ''},
                   {'device': 'bridge', 'nicModel': 'virtio',
                    'macAddr': '52:54:00:59:FF:FF', 'type': 'interface',
                    'network': ''},
                   {'device': 'rng', 'specParams': {'source': 'random'},
                    'model': 'virtio', 'type': 'rng'},
                   {'device': 'rng', 'specParams': {'source': 'random'},
                    'model': 'virtio', 'type': 'rng'}]

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
