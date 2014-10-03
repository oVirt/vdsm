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


from xml.dom import minidom

import caps
import supervdsm


def getVcpuPid(vmName):
    runFile = "/var/run/libvirt/qemu/%s.xml" % vmName
    runInfo = minidom.parse(runFile)
    vCpus = runInfo.getElementsByTagName('vcpus')[0]
    vCpuSet = vCpus.getElementsByTagName('vcpu')
    vCpuPids = {}
    for vCpuIndex, vCpu in enumerate(vCpuSet):
        vCpuPids[vCpuIndex] = vCpu.getAttribute('pid')
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
    if 'guestNumaNodes' in vm.conf:
        vCpuRuntimePinMap = _getVcpuRuntimePinMap(vm)
        if vCpuRuntimePinMap:
            vmName = vm.conf['vmName'].encode('utf-8')
            vCpuMemoryMapping = \
                supervdsm.getProxy().getVcpuNumaMemoryMapping(vmName)
            pNodesCpusMap = _getHostNumaNodesCpuMap()
            vNodesCpusMap = _getVmNumaNodesCpuMap(vm)
            for vCpu, pCpu in vCpuRuntimePinMap.iteritems():
                vNodeIndex = str(vNodesCpusMap[vCpu])
                if vNodeIndex not in vmNumaNodeRuntimeMap:
                    vmNumaNodeRuntimeMap[vNodeIndex] = []
                vmNumaNodeRuntimeMap[vNodeIndex].append(pNodesCpusMap[pCpu])
                if vCpu in vCpuMemoryMapping:
                    vmNumaNodeRuntimeMap[vNodeIndex].extend(
                        vCpuMemoryMapping[vCpu])
            vmNumaNodeRuntimeMap = dict([(k, list(set(v))) for k, v in
                                        vmNumaNodeRuntimeMap.iteritems()])
    return vmNumaNodeRuntimeMap


def _getVcpuRuntimePinMap(vm):
    vCpuRuntimePinMap = {}
    if vm._vmStats:
        sample = vm._vmStats.sampleVcpuPinning.getLastSample()
        vCpuInfos = sample if sample is not None else []
        for vCpuInfo in vCpuInfos:
            vCpuRuntimePinMap[vCpuInfo[0]] = vCpuInfo[3]
    return vCpuRuntimePinMap


def _getHostNumaNodesCpuMap():
    pNodesCpusMap = {}
    for nodeIndex, numaNode in caps.getNumaTopology().iteritems():
        for cpuId in numaNode['cpus']:
            pNodesCpusMap[cpuId] = int(nodeIndex)
    return pNodesCpusMap


def _getVmNumaNodesCpuMap(vm):
    vNodesCpusMap = {}
    for vmNumaNode in vm.conf['guestNumaNodes']:
        for vCpuId in map(int, vmNumaNode['cpus'].split(",")):
            vNodesCpusMap[vCpuId] = vmNumaNode['nodeIndex']
    return vNodesCpusMap
