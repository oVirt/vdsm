#
# Copyright 2011-2012 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from collections import defaultdict
import logging

import os.path
import xml.etree.cElementTree as ET

import caps
import supervdsm

# xml file name -> (last mtime, cached value)
_libvirt_vcpu_pids_cache = {}


def _libvirt_xml_path(vmName):
    return "/var/run/libvirt/qemu/%s.xml" % vmName


def invalidateNumaCache(vm):
    vmName = vm.name.encode('utf-8')
    path = _libvirt_xml_path(vmName)
    try:
        del _libvirt_vcpu_pids_cache[path]
    except KeyError:
        pass  # ignore


# TODO update to API calls once bug
#      https://bugzilla.redhat.com/show_bug.cgi?id=1211518
#      is resolved.
def getVcpuPid(vmName):
    path = _libvirt_xml_path(vmName)
    mtime = os.path.getmtime(path)

    try:
        if path in _libvirt_vcpu_pids_cache:
            lastmtime, value = _libvirt_vcpu_pids_cache[path]
            if lastmtime == mtime:
                return value
    except KeyError:
        # Make sure we do not crash if the cache is suddenly
        # invalidated
        pass

    runInfo = ET.parse(path)
    vCpuPids = {}
    for vCpuIndex, vCpu in enumerate(runInfo.findall('./vcpus/vcpu')):
        vCpuPids[vCpuIndex] = vCpu.get('pid')

    _libvirt_vcpu_pids_cache[path] = (mtime, vCpuPids)
    return vCpuPids


def getVmNumaNodeRuntimeInfo(vm):
    """
    Collect vm numa nodes runtime pinning to which host numa nodes
    information.
    Host numa node topology:
    'numaNodes': {'<nodeIndex>': {'cpus': [int], 'totalMemory': 'str'},
                  ...}
    We can get each physical cpu core belongs to which host numa node.

    Vm numa node configuration:
    'guestNumaNodes': [{'cpus': 'str', 'memory': 'str'}, ...]
    We can get each vcpu belongs to which vm numa node.

    Vcpu runtime pinning to physical cpu core information:
    ([(0, 1, 19590000000L, 1), (1, 1, 10710000000L, 1)],
     [(True, True, True, True), (True, True, True, True)])
    The first list element of the above tuple describe each vcpu(list[0])
    runtime pinning to which physical cpu core(list[3]).

    Get the mapping info between vcpu and pid from
    /var/run/libvirt/qemu/<vmName>.xml

    Get each vcpu(pid) backed memory mapping to which host numa nodes info
    from /proc/<vm_pid>/<vcpu_pid>/numa_maps

    From all the above information, we can calculate each vm numa node
    runtime pinning to which host numa node.
    The output is a map like:
    '<vm numa node index>': [<host numa node index>, ...]
    """

    vmNumaNodeRuntimeMap = {}

    vcpu_to_pcpu = _get_mapping_vcpu_to_pcpu(
        _get_vcpu_positioning(vm))
    if vcpu_to_pcpu:
        vm_numa_placement = defaultdict(set)

        vcpu_to_pnode = supervdsm.getProxy().getVcpuNumaMemoryMapping(
            vm.conf['vmName'].encode('utf-8'))
        pcpu_to_pnode = _get_mapping_pcpu_to_pnode()
        vcpu_to_vnode = _get_mapping_vcpu_to_vnode(vm)

        for vcpu_id, pcpu_id in vcpu_to_pcpu.iteritems():
            try:
                vnode_index = str(vcpu_to_vnode[vcpu_id])
            except KeyError:
                # Not all CPUs are mapped to NUMA nodes, e.g.:
                # - We don't assign hotplugged CPUs to NUMA nodes.
                # - When Engine assigns equal number of CPUs to each of the
                #   NUMA nodes, the contingent remaining CPUs are left
                #   unassigned.
                # We simply skip the unassigned CPUs here.
                log = logging.getLogger('NUMA')
                log.debug("Virtual CPU #%s not assigned to any virtual "
                          "NUMA node",
                          vcpu_id)
                continue
            vm_numa_placement[vnode_index].add(pcpu_to_pnode[pcpu_id])
            vm_numa_placement[vnode_index].update(
                vcpu_to_pnode.get(vcpu_id, ()))

        vmNumaNodeRuntimeMap = dict((k, list(v)) for k, v in
                                    vm_numa_placement.iteritems())

    return vmNumaNodeRuntimeMap


def _get_vcpu_positioning(vm):
    try:
        return vm._dom.vcpus()[0]
    except AttributeError:
        # _dom may be reset to none asynchronously
        return None


def _get_mapping_vcpu_to_pcpu(sample):
    vcpu_to_pcpu = {}
    # please note that here the naming is misleading.
    # these samples does not represent the *pinning*,
    # but rather last *positioning*
    infos = sample if sample is not None else []
    for (vcpu_id, _, _, pcpu_id) in infos:
        vcpu_to_pcpu[vcpu_id] = pcpu_id
    return vcpu_to_pcpu


def _get_mapping_pcpu_to_pnode():
    pcpu_to_pnode = {}
    for node_index, numa_node in caps.getNumaTopology().iteritems():
        for pcpu_id in numa_node['cpus']:
            pcpu_to_pnode[pcpu_id] = int(node_index)
    return pcpu_to_pnode


def _get_mapping_vcpu_to_vnode(vm):
    vcpu_to_vnode = {}
    for vm_numa_node in vm.conf['guestNumaNodes']:
        for vcpu_id in map(int, vm_numa_node['cpus'].split(",")):
            vcpu_to_vnode[vcpu_id] = vm_numa_node['nodeIndex']
    return vcpu_to_vnode
