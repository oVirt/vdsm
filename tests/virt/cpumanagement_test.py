# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import os.path

from vdsm import numa
from vdsm.common import libvirtconnection
from vdsm.virt import cpumanagement

from monkeypatch import MonkeyPatch


def _get_caps_data(file):
    testPath = os.path.realpath(__file__)
    dirName = os.path.dirname(testPath)
    dirName = os.path.dirname(dirName)
    path = os.path.join(dirName, file)
    with open(path) as src:
        return src.read()


def _getLibvirtConnStubFromFile(file):
    class ConnStub:
        def getCapabilities(self):
            return _get_caps_data(file)
    return ConnStub()


class FakeClientIF(object):
    def __init__(self, vmContainer):
        self.vmContainer = vmContainer

    def getVMs(self):
        return self.vmContainer


class FakeVM(object):
    def __init__(self, cpu_policy, pinned_cpus):
        self._cpu_policy = cpu_policy
        self._pinned_cpus = pinned_cpus

    def cpu_policy(self):
        return self._cpu_policy

    def pinned_cpus(self):
        return self._pinned_cpus


def test_libvirt_cpuset_spec():
    cpuset = cpumanagement.libvirt_cpuset_spec({2, 4}, 6)
    assert type(cpuset) == tuple
    assert len(cpuset) == 6
    assert cpuset == (False, False, True, False, True, False)


@MonkeyPatch(libvirtconnection, 'get',
             lambda: _getLibvirtConnStubFromFile(
                 'caps_libvirt_intel_E5649.out'))
@MonkeyPatch(numa, 'memory_by_cell', lambda x: {'total': '1', 'free': '1'})
def test_shared_pool():
    # 2 sockets, 6 cores per socket, 2 threads per core
    numa.update()
    core_cpus = numa.core_cpus()
    online_cpus = list(range(24))
    cif = FakeClientIF({
        0: FakeVM(cpumanagement.CPU_POLICY_NONE, {
            # included in shared pool
            0: frozenset([2]),
            1: frozenset([14]),
        }),
        1: FakeVM(cpumanagement.CPU_POLICY_MANUAL, {
            # included in shared pool
            0: frozenset([0]),
            1: frozenset([1]),
        }),
        2: FakeVM(cpumanagement.CPU_POLICY_DEDICATED, {
            0: frozenset([12]),
            1: frozenset([13]),
        }),
        3: FakeVM(cpumanagement.CPU_POLICY_ISOLATE_THREADS, {
            0: frozenset([4]),  # blocks also 16
            1: frozenset([6]),  # blocks also 18
        }),
        4: FakeVM(cpumanagement.CPU_POLICY_SIBLINGS, {
            0: frozenset([8]),
            1: frozenset([10]),
            2: frozenset([20]),  # blocks also 22
        }),
    })
    pool = cpumanagement._shared_pool(cif, online_cpus, core_cpus)
    assert pool == {0, 1, 2, 3, 5, 7, 9, 11, 14, 15, 17, 19, 21, 23}


@MonkeyPatch(libvirtconnection, 'get',
             lambda: _getLibvirtConnStubFromFile(
                 'caps_libvirt_intel_E5649.out'))
@MonkeyPatch(numa, 'memory_by_cell', lambda x: {'total': '1', 'free': '1'})
def test_siblings():
    # 2 sockets, 6 cores per socket, 2 threads per core
    numa.update()
    cpus = numa.core_cpus()
    assert cpumanagement._siblings(cpus, 0) == frozenset([12])
    assert cpumanagement._siblings(cpus, 8) == frozenset([20])


@MonkeyPatch(libvirtconnection, 'get',
             lambda: _getLibvirtConnStubFromFile(
                 'caps_libvirt_intel_E31220.out'))
@MonkeyPatch(numa, 'memory_by_cell', lambda x: {'total': '1', 'free': '1'})
def test_siblings_no_smt():
    # 1 socket, 4 cores per socket, 1 threads per core
    numa.update()
    cpus = numa.core_cpus()
    assert cpumanagement._siblings(cpus, 0) == frozenset()
