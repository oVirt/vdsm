#
# Copyright 2017-2019 Red Hat, Inc.
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

import os
import tempfile

from unittest import mock

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase as TestCaseBase
from testlib import make_config
from testlib import namedTemporaryDir
from testlib import permutations, expandPermutations

from virt import vmfakelib as fake

from vdsm import hugepages
from vdsm import osinfo
from vdsm.common import cpuarch
from vdsm.common import supervdsm
from vdsm.supervdsm_api import virt
from vdsm.virt import vm

_STATE = {
    'resv_hugepages': 1234,
    'free_hugepages': 1234,
    'nr_overcommit_hugepages': 1234,
    'surplus_hugepages': 1234,
    'nr_hugepages': 1234,
    'nr_hugepages_mempolicy': 1234,
    'vm.free_hugepages': 1234
}

_VM_HUGEPAGES_METADATA = '''
<ovirt-vm:vm>
   <ovirt-vm:custom>
     <ovirt-vm:hugepages>{hugepages}</ovirt-vm:hugepages>
   </ovirt-vm:custom>
</ovirt-vm:vm>
'''


@expandPermutations
class TestHugepages(TestCaseBase):

    @permutations([
        [b'1024', 1024, 1024],
        [b'1024', -1024, -1024],
        [b'1024', -512, -512],
        [b'1024', 0, 0],
    ])
    @MonkeyPatch(hugepages, '_size_from_dir', lambda x: x)
    @MonkeyPatch(hugepages, 'state', lambda: {2048: _STATE})
    @MonkeyPatch(supervdsm, 'getProxy', lambda: virt)
    def test_alloc(self, default, count, expected):
        with tempfile.NamedTemporaryFile() as f:
            f.write(default)
            f.flush()
            ret = hugepages._alloc(count, size=2048, path=f.name)
            f.seek(0)
            self.assertEqual(ret, expected)

    @MonkeyPatch(hugepages, '_size_from_dir', lambda x: x)
    def test_supported(self):
        with namedTemporaryDir() as src:
            # A list of 3 file names, where the files are temporary.
            sizes = [os.path.basename(f.name) for f in [
                tempfile.NamedTemporaryFile(
                    dir=src, delete=False
                ) for _ in range(3)
            ]]
            with mock.patch('{}.open'.format(hugepages.__name__),
                            mock.mock_open(read_data='0'),
                            create=True):

                self.assertEqual(set(hugepages.supported(src)), set(sizes))

    @MonkeyPatch(hugepages, '_size_from_dir', lambda x: x)
    def test_state(self):
        with namedTemporaryDir() as src:
            # A list of 3 file names, where the files are temporary.
            sizes = [os.path.basename(f.name) for f in [
                tempfile.NamedTemporaryFile(
                    dir=src, delete=False
                ) for _ in range(3)
            ]]
            with mock.patch('{}.open'.format(hugepages.__name__),
                            mock.mock_open(read_data='1234'),
                            create=True):

                self.assertEqual(len(hugepages.state(src)), len(sizes))
                for value in hugepages.state(src).values():
                    self.assertEqual(value, _STATE)

    @permutations([
        ['hugepages-2048Kb', 2048],
        ['hugepages-10000Kb', 10000],
        ['hugepages-1Kb', 1],
    ])
    def test_size_from_dir(self, filename, expected):
        self.assertEqual(hugepages._size_from_dir(filename), expected)


class TestIntelligentAllocation(TestCaseBase):

    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "true"),
                     ("performance", "reserved_hugepage_count", "9"),
                     ("performance", "reserved_hugepage_size", "2048"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {2048: {'nr_hugepages': 12,
                         'free_hugepages': 12}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    def test_allocation_1_page(self):
        vm_hugepagesz = 2048
        vm_hugepages = 4
        vdsm_vms = vm_hugepages + 0

        cif = FakeClientIF({0: FakeVM(vdsm_vms, vm_hugepagesz)})

        # We should allocate 1 new hugepage:
        # - 12 total (and also free) pages
        # - 9 pages are reserved
        # - vm requires 4 pages; we allocate 1 to avoid touching reserved pages
        self.assertEqual(hugepages.calculate_required_allocation(
            cif, vm_hugepages, vm_hugepagesz), 1
        )

    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "true"),
                     ("performance", "reserved_hugepage_count", "4"),
                     ("performance", "reserved_hugepage_size", "2048"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {2048: {'nr_hugepages': 8,
                         'free_hugepages': 4}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    def test_allocation_4_pages(self):
        vm_hugepagesz = 2048
        vm_hugepages = 4
        vdsm_vms = vm_hugepages + 4

        cif = FakeClientIF({0: FakeVM(vdsm_vms, vm_hugepagesz)})

        # We expect 4 new hugepages:
        # - there are 8 hugepages
        # - 4 are free, 4 used by vdsm, 4 reserved -> the free ones are
        #   reserved
        # - vm requires 4 new hugepages; we can't touch reserved pages
        self.assertEqual(hugepages.calculate_required_allocation(
            cif, vm_hugepages, vm_hugepagesz), 4
        )

    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "true"),
                     ("performance", "reserved_hugepage_count", "12"),
                     ("performance", "reserved_hugepage_size", "2048"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {2048: {'nr_hugepages': 16,
                         'free_hugepages': 4}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    def test_allocation_0_pages(self):
        vm_hugepagesz = 2048
        vm_hugepages = 4
        vdsm_vms = vm_hugepages + 0

        cif = FakeClientIF({0: FakeVM(vdsm_vms, vm_hugepagesz)})

        # We expect no new hugepages:
        # - there are 4 free hugepages
        # - vdsm doesn't use any pages (yet)
        # - 12 are reserved and used
        self.assertEqual(hugepages.calculate_required_allocation(
            cif, vm_hugepages, vm_hugepagesz), 0
        )

    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "true"),
                     ("performance", "reserved_hugepage_count", "12"),
                     ("performance", "reserved_hugepage_size", "2048"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {2048: {'nr_hugepages': 16,
                         'free_hugepages': 4}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    def test_allocation_0_pages_mixedenv(self):
        # Simulate the code, 0 means that the VM doesn't have hugepages...
        vm_hugepagesz = 0
        # but let's introduce something that would normally throw us off.
        vm_hugepages = 4
        vdsm_vms = vm_hugepages + 0

        cif = FakeClientIF({0: FakeVM(vdsm_vms, vm_hugepagesz)})

        # We expect no new hugepages:
        # - there are 4 free hugepages
        # - vdsm doesn't use any pages (yet)
        # - 12 are reserved and used
        self.assertEqual(hugepages.calculate_required_allocation(
            cif, vm_hugepages, vm_hugepagesz), 0
        )

    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "true"),
                     ("performance", "reserved_hugepage_count", "4"),
                     ("performance", "reserved_hugepage_size", "1048576"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {2048: {'nr_hugepages': 4,
                         'free_hugepages': 4}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    def test_allocation_different_size_reserved(self):
        vm_hugepagesz = 2048
        vm_hugepages = 4

        cif = FakeClientIF({0: FakeVM(12, 1048576)})

        # We expect no new hugepages:
        # - pages of different size are reserved
        # - VMs that exist use different hugepage size
        # - we have 4 free hugepages of correct size
        self.assertEqual(hugepages.calculate_required_allocation(
            cif, vm_hugepages, vm_hugepagesz), 0
        )

    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "false"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {2048: {'nr_hugepages': 4,
                         'free_hugepages': 4}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    def test_pure_dynamic_hugepages(self):
        vm_hugepagesz = 2048
        vm_hugepages = 4

        cif = FakeClientIF({0: FakeVM(0, 1048576)})

        # Fully dynamic, allocate pages for whole VM.
        self.assertEqual(hugepages.calculate_required_allocation(
            cif, vm_hugepages, vm_hugepagesz), 4
        )


class TestIntelligentDeallocation(TestCaseBase):

    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "true"),
                     ("performance", "reserved_hugepage_count", "13"),
                     ("performance", "reserved_hugepage_size", "1048576"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {1048576: {'nr_hugepages': 17,
                            'free_hugepages': 0}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    @MonkeyPatch(osinfo, 'kernel_args_dict', lambda:
                 {'hugepagesz': '1G', 'hugepages': '16'})
    def test_deallocation_1_page(self):
        vm_hugepagesz = 1048576
        vm_hugepages = 4

        self.assertEqual(hugepages.calculate_required_deallocation(
            vm_hugepages, vm_hugepagesz), 1
        )

    # - 17 pages in the system, 16 allocated on boot time
    # - VM uses 4 pages, 13 are reserved
    # - since we don't touch boot-time allocated pages, we're only able to
    #   deallocate a single page
    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "true"),
                     ("performance", "reserved_hugepage_count", "12"),
                     ("performance", "reserved_hugepage_size", "1048576"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {1048576: {'nr_hugepages': 17,
                            'free_hugepages': 0}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    @MonkeyPatch(osinfo, 'kernel_args_dict', lambda:
                 {})
    def test_deallocation_4_pages_no_cmdline(self):
        vm_hugepagesz = 1048576
        vm_hugepages = 4

        # There are 17 pages in the system, none of which were allocated on
        # boot.
        # - the VM consumed 4 pages, no other consumption
        # - there are 12 pages reserved
        # - that means we could deallocate up to 5 pages, but we don't touch
        #   pages out of VM's domain - therefore deallocating only 4 pages
        self.assertEqual(hugepages.calculate_required_deallocation(
            vm_hugepages, vm_hugepagesz), 4
        )

    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "true"),
                     ("performance", "reserved_hugepage_count", "12"),
                     ("performance", "reserved_hugepage_size", "1048576"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {1048576: {'nr_hugepages': 20,
                            'free_hugepages': 0}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    @MonkeyPatch(osinfo, 'kernel_args_dict', lambda:
                 {'hugepagesz': '1G', 'hugepages': '16'})
    def test_deallocation_4_pages(self):
        vm_hugepagesz = 1048576
        vm_hugepages = 4

        # The VM was solely in the dynamic allocation space (16 preallocated,
        # 12 reserved but 20 total), we can fully deallocate it.
        self.assertEqual(hugepages.calculate_required_deallocation(
            vm_hugepages, vm_hugepagesz), 4
        )

    @MonkeyPatch(hugepages, 'config',
                 make_config([
                     ("performance", "use_preallocated_hugepages", "false"),
                 ]))
    @MonkeyPatch(hugepages, 'state', lambda:
                 {1048576: {'nr_hugepages': 16,
                            'free_hugepages': 16}
                  })
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    @MonkeyPatch(osinfo, 'kernel_args_dict', lambda:
                 {'hugepagesz': '1G', 'hugepages': '16'})
    def test_pure_dynamic_hugepages(self):
        vm_hugepagesz = 1048576
        vm_hugepages = 4

        # Fully dynamic deallocation (= deallocate the size of the VM)
        self.assertEqual(hugepages.calculate_required_deallocation(
            vm_hugepages, vm_hugepagesz), 4
        )


@expandPermutations
class TestVmHugepages(TestCaseBase):
    @permutations([
        [-1, False],
        [0, False],
        [1, True],
        [2048, True],
        [1048576, True],
    ])
    def test_hugepages_allowed(self, hugepages, expected):
        metadata = _VM_HUGEPAGES_METADATA.format(hugepages=hugepages)
        with fake.VM(metadata=metadata) as vm:
            self.assertEqual(vm.hugepages, expected)

    @permutations([
        [1, 2048],
        [2048, 2048],
        [1048576, 1048576],
        [10485760, 2048],
    ])
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    @MonkeyPatch(hugepages, 'supported', lambda: [2048, 1048576])
    def test_hugepagesz(self, hugepages, expected):
        metadata = _VM_HUGEPAGES_METADATA.format(hugepages=hugepages)
        with fake.VM(metadata=metadata) as vm:
            self.assertEqual(vm.hugepagesz, expected)

    @permutations([
        [1, 1, 1],
        [1, 3, 2],
        [1048576, 1023, 1],
        [1048576, 1025, 2],
    ])
    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.X86_64)
    @MonkeyPatch(hugepages, 'supported', lambda: [2048, 1048576])
    def test_nr_hugepages(self, hugepages, memory, expected):
        with mock.patch.object(vm.Vm, 'mem_size_mb', lambda _: memory):
            metadata = _VM_HUGEPAGES_METADATA.format(hugepages=hugepages)
            with fake.VM(metadata=metadata) as fakevm:
                self.assertEqual(fakevm.nr_hugepages, expected)


class FakeClientIF(object):
    def __init__(self, vmContainer):
        self.vmContainer = vmContainer

    def getVMs(self):
        return self.vmContainer


class FakeVM(object):
    def __init__(self, hugepages, hugepagesz):
        self.nr_hugepages = hugepages
        self.hugepagesz = hugepagesz
