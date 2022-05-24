# Copyright 2021 Red Hat, Inc.
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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Refer to the README and COPYING files for full details of the license
#

import libvirt
import threading
import xml.etree.ElementTree as ET

from vdsm import numa
from vdsm.virt import virdomain
from vdsm.virt import vmxml

# no policy defined, CPUs from shared pool will be used
CPU_POLICY_NONE = "none"
# each vCPU is pinned to single pCPU that cannot be used by any other VM
CPU_POLICY_DEDICATED = "dedicated"
# like siblings below but only one vCPU can be assigned to each physical
# core
CPU_POLICY_ISOLATE_THREADS = "isolate-threads"
# manual CPU pinning or NUMA auto-pinning policy
CPU_POLICY_MANUAL = "manual"
# like dedicated but physical cores used by the VM are blocked from use by
# other VMs
CPU_POLICY_SIBLINGS = "siblings"

# Lock to prevent two concurrent updates of shared CPU pool
_shared_pool_lock = threading.Lock()


def on_vm_create(vm_obj):
    """
    Assign CPUs on VM start. At the moment this only takes care of VMs with no
    CPU policy or pinning.

    :param vm_obj: VM object of the newly created VM. The object has to be
      already in the VM container.
    :type vm_obj: vdsm.virt.VM instance
    """
    if vm_obj.cpu_policy() == CPU_POLICY_NONE:
        vm_obj.log.debug('Configuring CPUs')
        _assign_shared(vm_obj.cif, vm_obj)
    else:
        _assign_shared(vm_obj.cif)


def on_vm_change(vm_obj):
    """
    Update shared CPU pool after change to CPU pining of a VM.

    :param vm_obj: VM object of the changed VM.
    :type vm_obj: vdsm.virt.Vm instance
    """
    _assign_shared(vm_obj.cif)


def on_vm_destroy(vm_obj):
    """
    Update shared CPU pool when destroying a VM.

    :param vm_obj: The VM being destroyed. It is expected that VM object is no
      longer in the VM container.
    :type vm_obj: vdsm.virt.VM instance
    """
    if vm_obj.cpu_policy() in (CPU_POLICY_NONE, CPU_POLICY_MANUAL):
        # Shared policy VM, nothing to do
        vm_obj.log.debug('Removing %s policy VM', vm_obj.cpu_policy())
        return
    vm_obj.log.debug(
        'Removing %s policy VM (freeing cpus=%r)',
        vm_obj.cpu_policy(), vm_obj.pinned_cpus())
    _assign_shared(vm_obj.cif)


def _assign_shared(cif, target_vm=None):
    """
    Assign all CPUs from shared pool to all VMs with no policy or to
    a specific VM with no policy.

    :param target_vm: A VM instance, CPUs of which are to be configured with
      shared pool CPU set. If None, all VMs with no specific policy will be
      reconfigured with current shared pool CPU set.
    :type target_vm: vdsm.virt.VM or None
    """
    numa.update()
    core_cpus = numa.core_cpus()
    cpu_topology = numa.cpu_topology()
    cpu_list_length = max(cpu_topology.online_cpus) + 1

    with _shared_pool_lock:
        shared_cpus = _shared_pool(cif, cpu_topology.online_cpus, core_cpus)
        shared_str = ','.join(map(str, shared_cpus))
        cpuset = libvirt_cpuset_spec(shared_cpus, cpu_list_length)
        if target_vm is None:
            vms_to_update = cif.getVMs().values()
        else:
            vms_to_update = [target_vm]
        for vm in vms_to_update:
            if vm.cpu_policy() not in (CPU_POLICY_NONE, CPU_POLICY_MANUAL):
                continue
            try:
                for vcpu in range(vm.get_number_of_cpus()):
                    if (vm.cpu_policy() == CPU_POLICY_MANUAL and
                            vcpu in vm.manually_pinned_cpus()):
                        continue
                    vm.log.debug(
                        'configuring vCPU=%d with cpuset="%s"',
                        vcpu, shared_str)
                    try:
                        vm.pin_vcpu(vcpu, cpuset)
                    except virdomain.NotConnectedError:
                        vm.log.warning(
                            "Cannot reconfigure CPUs, domain not connected.")
                    except libvirt.libvirtError as e:
                        if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                            vm.log.warning(
                                'Cannot reconfigure CPUs,'
                                ' domain does not exist anymore.')
                        else:
                            raise
            except:
                vm.log.exception(
                    'Failed to update CPU set of the VM to match shared pool')
                # Even if this VM failed, proceed and try to configure shared
                # pool for other VMs.


def _flatten_cpusets(cpusets_dict):
    """
    Convert dictionary with cpusets (key: vCPU ID, value: frozenset) into a
    list where on index <vCPU_ID> is string description of cpuset if such is
    specified in the dictionary and None otherwise. The first form is used
    when parsing libvirt domain XML and the second form is used in VDSM API
    calls.

    :param cpusets_dict: Dictionary with CPU sets
    :type cpusets_dict: dict

    :returns: list of strings
    """
    if len(cpusets_dict.keys()) == 0:
        return []
    result = [None] * (max(cpusets_dict.keys()) + 1)
    for cpu_id, cpuset in cpusets_dict.items():
        result[cpu_id] = ",".join(map(str, cpuset))
    return result


def libvirt_cpuset_spec(cpus, cpu_list_length):
    """
    Turn set of CPU IDs to a CPU set list for libvirt API. That is a tuple of
    boolean values where True value on index i means that CPU with ID i is
    part of the CPU set.

    Note that libvirt API expects a tuple and it does not accept just any
    iterable.

    :param cpus: Set (or any iterable) of CPU indices that are part of the
      resulting set.
    :type cpus: iterable
    :param cpu_list_length: Length of the returned list. The caller must make
      sure that the length is not smaller than maximal index in cpus.
    :type cpu_list_length: int

    :returns: tuple of booleans
    """
    cpuset = [False] * cpu_list_length
    for cpu in cpus:
        cpuset[cpu] = True
    return tuple(cpuset)


def replace_cpu_pinning(vm, dom, target_vcpupin):
    """
    Replace <vcpupin> elements in <cputune>. This removes all vCPU pinning
    added by VDSM and honors list of manaually pinned CPUs. It then adds and
    replaces remainging <vcpupin> elements to match requested vCPU pinning.

    :param vm: associated VM object
    :type vm: vdsm.virt.VM
    :param dom: DOM of the libvirt XML
    :type dom: xml.etree.ElementTree.Element
    :param target_vcpupin: Dictionary describing requested vCPU pinning --
      key: vCPU ID, value: cpuset.
    :type target_vcpupin: dict
    """
    cputune = dom.find('cputune')
    if cputune is not None:
        for vcpu in vmxml.find_all(cputune, 'vcpupin'):
            vcpu_id = int(vcpu.get('vcpu'))
            if (vm.cpu_policy() == CPU_POLICY_MANUAL
                    and vcpu_id in vm.manually_pinned_cpus()):
                continue
            cputune.remove(vcpu)
    # Reconfigure CPU pinning based on the call parameter
    if target_vcpupin is not None and len(target_vcpupin) > 0:
        if type(target_vcpupin) == dict:
            target_vcpupin = _flatten_cpusets(target_vcpupin)
        else:
            # Make sure we don't modify original list
            target_vcpupin = list(target_vcpupin)
        if cputune is None:
            cputune = ET.Element('cputune')
            dom.append(cputune)
        # First modify existing elements
        for vcpupin in vmxml.find_all(cputune, 'vcpupin'):
            vcpu_id = int(vcpupin.get('vcpu'))
            if vcpu_id >= 0 and vcpu_id < len(target_vcpupin):
                vcpupin.set('cpuset',
                            str(target_vcpupin[vcpu_id]))
                target_vcpupin[vcpu_id] = None
        # Now create elements for pinning that was not there before.
        # This should happen only for pinning that was removed above. It
        # should not happen for manual CPU pinning because it would render
        # the value of manuallyPinedCPUs metadata invalid.
        for vcpu_id, cpuset in enumerate(target_vcpupin):
            if cpuset is None:
                continue
            vcpupin = ET.Element('vcpupin')
            vcpupin.set('vcpu', str(vcpu_id))
            vcpupin.set('cpuset', str(cpuset))
            cputune.append(vcpupin)
    return dom


def _shared_pool(cif, online_cpus, core_cpus):
    """
    IDs of CPUs shared among all VMs with shared policy

    :param online_cpus: Indices of all on-line CPUs.
    :type online_cpus: list
    :param core_cpus: Mapping between a core and a set of IDs of CPUs on that
      core: (socket_id, die_id, core_id) -> set()
    :type core_cpus: dict

    :returns: set of CPU IDs of CPUs in shared pool.
    """
    shared = set(online_cpus)
    for vm in cif.getVMs().values():
        if vm.cpu_policy() in (CPU_POLICY_NONE, CPU_POLICY_MANUAL):
            continue
        blocked = set().union(*vm.pinned_cpus().values())
        if vm.cpu_policy() == CPU_POLICY_ISOLATE_THREADS or \
                vm.cpu_policy() == CPU_POLICY_SIBLINGS:
            for cpu in blocked.copy():
                blocked |= set(_siblings(core_cpus, cpu))
        shared -= blocked
    return shared


def _siblings(core_cpus, cpu):
    """
    Returns all siblings of the requested CPU.

    :param core_cpus: Mapping between a core and a set of IDs of CPUs on that
      core: (socket_id, die_id, core_id) -> set()
    :type core_cpus: dict
    :param cpu: ID of a CPU for which to get all siblings.
    :type cpu: int

    :returns: Frozenset with all siblings of requested CPU. The requested CPU
      is not included in the set.
    :raises: IndexError if given CPU ID was not found.
    """
    for core in core_cpus.values():
        if cpu in core:
            return frozenset(core) - frozenset([cpu])
    return IndexError('No such CPU %s' % cpu)
