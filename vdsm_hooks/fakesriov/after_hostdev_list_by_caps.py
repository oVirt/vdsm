#!/usr/bin/python
#
# Copyright 2015 Red Hat, Inc.
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

"""
To Enable this set fake_sriov_enable=true in /etc/vdsm/vdsm.conf.
"""
import hooking
from vdsm.config import config

_PF_DEVICE_NAME = 'enp22s0f0'
_VF0_DEVICE_NAME = 'enp22s16'
_VF1_DEVICE_NAME = 'enp22s16f2'
_PCI_ADDR_PF = 'pci_0000_22_00_0'
_PCI_ADDR_VF0 = 'pci_0000_22_10_0'
_PCI_ADDR_VF1 = 'pci_0000_22_10_2'
_FAKE_SRIOV = {
    # sriov device
    _PCI_ADDR_PF: {
        'params': {
            'address': {'bus': '22', 'domain': '0', 'function': '0',
                        'slot': '0'},
            'capability': 'pci', 'iommu_group': '15',
            'parent': 'pci_0000_00_03_0',
            'product': '82576 Gigabit Network Connection',
            'product_id': '0x10e7', 'totalvfs': 7,
            'vendor': 'Intel Corporation', 'vendor_id': '0x8086'}},
    # sriov net device
    'net_%s_78_e7_d1_e4_8f_16' % _PF_DEVICE_NAME: {
        'params': {
            'capability': 'net', 'interface': _PF_DEVICE_NAME,
            'parent': _PCI_ADDR_PF}},
    # vf0
    _PCI_ADDR_VF0: {
        'params': {
            'address': {'bus': '22', 'domain': '0', 'function': '0',
                        'slot': '16'},
            'capability': 'pci', 'iommu_group': '23',
            'parent': 'pci_0000_00_03_0', 'physfn': _PCI_ADDR_PF,
            'product': '82576 Virtual Function', 'product_id': '0x10ca',
            'vendor': 'Intel Corporation', 'vendor_id': '0x8086'}},
    # vf0 net device
    'net_%s_aa_8c_31_98_8a_9a' % _VF0_DEVICE_NAME: {
        'params': {
            'capability': 'net', 'interface': _VF0_DEVICE_NAME,
            'parent': _PCI_ADDR_VF0}},
    # vf1
    _PCI_ADDR_VF1: {
        'params': {
            'address': {'bus': '22', 'domain': '0', 'function': '2',
                        'slot': '16'},
            'capability': 'pci', 'iommu_group': '24',
            'parent': 'pci_0000_00_03_0', 'physfn': _PCI_ADDR_PF,
            'product': '82576 Virtual Function', 'product_id': '0x10ca',
            'vendor': 'Intel Corporation', 'vendor_id': '0x8086'}},
    # vf1 net device
    'net_%s_ce_1f_0b_d1_ca_93' % _VF1_DEVICE_NAME: {
        'params': {
            'capability': 'net', 'interface': _VF1_DEVICE_NAME,
            'parent': _PCI_ADDR_VF1}}}

if __name__ == '__main__':
    if config.getboolean('vars', 'fake_sriov_enable'):
        host_devices = hooking.read_json()
        host_devices.update(_FAKE_SRIOV)
        hooking.write_json(host_devices)
