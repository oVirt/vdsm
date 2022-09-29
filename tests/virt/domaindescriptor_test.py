# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import xmlutils
from vdsm.virt.domain_descriptor import (DomainDescriptor,
                                         MutableDomainDescriptor)
from testlib import VdsmTestCase, XMLTestCase, permutations, expandPermutations


NO_DEVICES = """
<domain>
    <uuid>xyz</uuid>
</domain>
"""

EMPTY_DEVICES = """
<domain>
    <uuid>xyz</uuid>
    <devices/>
</domain>
"""

SOME_DEVICES = """
<domain>
    <uuid>xyz</uuid>
    <devices>
        <device name="foo"/>
        <device name="bar"/>
    </devices>
</domain>
"""

# made-up XML similar to libvirt domain XML
SOME_DISK_DEVICES = """
<domain>
    <uuid>xyz</uuid>
    <devices>
        <disk device="disk" name="vda"/>
        <disk device="disk"/>
        <disk device="cdrom"/>
    </devices>
</domain>
"""

REORDERED_DEVICES = """
<domain>
    <uuid>xyz</uuid>
    <devices>
        <device name="bar"/>
        <device name="foo"/>
    </devices>
</domain>
"""

MEMORY_SIZE = """
<domain>
    <uuid>xyz</uuid>
    <memory unit=\'KiB\'>1048576</memory>
    <devices>
        <device name="bar"/>
        <device name="foo"/>
    </devices>
</domain>
"""

METADATA = """
<domain>
    <uuid>xyz</uuid>
    <metadata>
        <foo>bar</foo>
    </metadata>
</domain>
"""

ON_REBOOT_DESTROY = """
<domain>
    <uuid>xyz</uuid>
    <on_reboot>destroy</on_reboot>
</domain>
"""

ON_REBOOT_RESTART = """
<domain>
    <uuid>xyz</uuid>
    <on_reboot>restart</on_reboot>
</domain>
"""

NO_REBOOT = """
<domain>
    <uuid>xyz</uuid>
</domain>
"""

VCPU = """
<domain>
    <vcpu>10</vcpu>
</domain>
"""

VCPU_CURRENT = """
<domain>
    <vcpu current="5">10</vcpu>
</domain>
"""

PINNED_CPUS = """
<domain>
    <cputune>
        <vcpupin vcpu="0" cpuset="1,2,5-7" />
        <vcpupin vcpu="1" cpuset="1,6,10" />
    </cputune>
</domain>
"""

NO_PINNED_CPUS = """
<domain>
    <uuid>xyz</uuid>
</domain>
"""


class DevicesHashTests(VdsmTestCase):

    def test_no_devices(self):
        desc1 = DomainDescriptor(NO_DEVICES)
        desc2 = DomainDescriptor(EMPTY_DEVICES)
        assert desc1.devices_hash != desc2.devices_hash

    def test_different_devices(self):
        desc1 = DomainDescriptor(EMPTY_DEVICES)
        desc2 = DomainDescriptor(SOME_DEVICES)
        assert desc1.devices_hash != desc2.devices_hash

    def test_different_order(self):
        desc1 = DomainDescriptor(SOME_DEVICES)
        desc2 = DomainDescriptor(REORDERED_DEVICES)
        assert desc1.devices_hash != desc2.devices_hash

    def test_stable_hash(self):
        desc1 = DomainDescriptor(SOME_DEVICES)
        desc2 = DomainDescriptor(SOME_DEVICES)
        assert desc1.devices_hash == desc2.devices_hash


@expandPermutations
class DomainDescriptorTests(XMLTestCase):

    @permutations([[NO_DEVICES, None],
                   [EMPTY_DEVICES, None],
                   [MEMORY_SIZE, 1024]])
    def test_memory_size(self, domain_xml, result):
        desc = DomainDescriptor(domain_xml)
        assert desc.get_memory_size() == result

    @permutations([[DomainDescriptor], [MutableDomainDescriptor]])
    def test_xml(self, descriptor):
        desc = descriptor(SOME_DEVICES)
        self.assertXMLEqual(desc.xml, SOME_DEVICES)

    @permutations([[DomainDescriptor, 'device', 2],
                   [DomainDescriptor, 'nonexistent', 0],
                   [MutableDomainDescriptor, 'device', 2],
                   [MutableDomainDescriptor, 'nonexistent', 0]])
    def test_device_elements(self, descriptor, tag, result):
        desc = descriptor(SOME_DEVICES)
        assert len(list(desc.get_device_elements(tag))) == result

    @permutations([
        [DomainDescriptor, 'device', {}, 2],
        [DomainDescriptor, 'device', {'name': 'foo'}, 1],
        [DomainDescriptor, 'device', {'inexistent': 'attr'}, 0],
        [DomainDescriptor, 'nonexistent', {}, 0],
        [MutableDomainDescriptor, 'device', {}, 2],
        [MutableDomainDescriptor, 'device', {'name': 'foo'}, 1],
        [MutableDomainDescriptor, 'device', {'inexistent': 'attr'}, 0],
        [MutableDomainDescriptor, 'nonexistent', {}, 0]
    ])
    def test_device_elements_with_attrs(self, descriptor, tag, attrs,
                                        expected):
        desc = descriptor(SOME_DEVICES)
        assert len(list(
            desc.get_device_elements_with_attrs(tag, **attrs)
        )) == expected

    @permutations([
        # attrs, expected_devs
        [{}, 3],
        [{'device': 'disk'}, 2],
        [{'device': 'cdrom'}, 1],
        [{'device': 'disk', 'name': 'vda'}, 1],
    ])
    def test_device_element_with_attrs_selection(self, attrs, expected_devs):
        desc = DomainDescriptor(SOME_DISK_DEVICES)
        assert len(list(
            desc.get_device_elements_with_attrs('disk', **attrs)
        )) == expected_devs

    @permutations([
        # xml_data, expected
        [MEMORY_SIZE, False],
        [METADATA, True],
    ])
    def test_metadata(self, xml_data, expected):
        desc = DomainDescriptor(xml_data)
        found = desc.metadata is not None
        assert found == expected

    @permutations([
        # values, expected_metadata
        [{'foo': 'baz'},
         """<?xml version='1.0' encoding='utf-8'?>
         <metadata xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
           <ovirt-vm:vm>
             <ovirt-vm:foo>baz</ovirt-vm:foo>
           </ovirt-vm:vm>
         </metadata>"""],
        [{'foo': 'bar', 'answer': 42},
         """<?xml version='1.0' encoding='utf-8'?>
         <metadata xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
           <ovirt-vm:vm>
             <ovirt-vm:answer type="int">42</ovirt-vm:answer>
             <ovirt-vm:foo>bar</ovirt-vm:foo>
           </ovirt-vm:vm>
         </metadata>"""],
    ])
    def test_metadata_descriptor(self, values, expected_metadata):
        desc = MutableDomainDescriptor(METADATA)
        with desc.metadata_descriptor() as md:
            with md.values() as vals:
                vals.update(values)

        desc2 = DomainDescriptor(desc.xml)
        self.assertXMLEqual(
            expected_metadata,
            xmlutils.tostring(desc2.metadata, pretty=True)
        )

    @permutations([
        [ON_REBOOT_DESTROY, 'destroy'],
        [ON_REBOOT_RESTART, 'restart'],
        [NO_REBOOT, None]
    ])
    def test_on_reboot_config(self, xml_data, expected):
        desc = DomainDescriptor(xml_data)
        reboot_config = desc.on_reboot_config()
        assert reboot_config == expected

    @permutations([
        [VCPU, 10],
        [VCPU_CURRENT, 5],
    ])
    def test_get_number_of_cpus(self, xml_data, expected):
        desc = DomainDescriptor(xml_data)
        cpus = desc.get_number_of_cpus()
        assert cpus == expected

    def test_pinned_cpus(self):
        desc = DomainDescriptor(PINNED_CPUS)
        pinning = desc.pinned_cpus
        assert len(pinning) == 2
        assert pinning[0] == frozenset([1, 2, 5, 6, 7])
        assert pinning[1] == frozenset([1, 6, 10])

    def test_no_pinned_cpus(self):
        desc = DomainDescriptor(NO_PINNED_CPUS)
        pinning = desc.pinned_cpus
        assert pinning == {}
