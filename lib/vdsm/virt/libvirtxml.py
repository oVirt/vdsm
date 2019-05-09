#
# Copyright 2008-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from vdsm.common import cpuarch
from vdsm.common import xmlutils
from vdsm.virt import vmxml
from vdsm import taskset


_BOOT_MENU_TIMEOUT = 10000  # milliseconds

_DEFAULT_MACHINES = {
    cpuarch.X86_64: 'pc',
    cpuarch.PPC64: 'pseries',
    cpuarch.PPC64LE: 'pseries',
    cpuarch.S390X: 's390-ccw-virtio',
}


def parse_domain(dom_xml, arch):
    """
    Rebuild a conf dictionary out of a dom_xml
    """
    conf = {'kvmEnable': 'true'}
    dom = xmlutils.fromstring(dom_xml)
    _parse_domain_init(dom, conf)
    _parse_domain_clock(dom, conf)
    _parse_domain_os(dom, conf)
    _parse_domain_cpu(dom, conf, arch)
    _parse_domain_input(dom, conf)
    # TODO: numatune
    return conf


def _parse_domain_init(dom, conf):
    iothreads = dom.findtext('./iothreads', default=None)
    if iothreads is not None:
        conf['numOfIoThreads'] = iothreads
    max_mem = dom.find('./maxMemory')
    if max_mem is not None:
        conf['maxMemSize'] = int(max_mem.text) // 1024
        conf['maxMemSlots'] = int(max_mem.attrib.get('slots', 16))
    smp = _parse_vcpu_element(dom.find('./vcpu'))
    if smp is not None:
        conf['smp'] = smp


def _parse_vcpu_element(vcpu):
    smp = None
    if vcpu is not None:
        smp = vcpu.attrib.get('current')
        if smp is None:
            # we expect the 'current' attribute, but we can still
            # extract meaningful information, so we go ahead.
            smp = vcpu.text
    return smp


def _parse_domain_clock(dom, conf):
    clock = dom.find('./clock')
    if clock is not None:
        conf['timeOffset'] = clock.attrib.get('adjustment', 0)
    if dom.find("./clock/timer[@name='hypervclock']"):
        conf['hypervEnable'] = 'true'


def _parse_domain_os(dom, conf):
    os = dom.find('./os/type')
    if os is not None:
        conf['emulatedMachine'] = os.attrib['machine']

    # TODO: do we need 'boot'?
    for param in ('initrd', 'kernel', 'kernelArgs'):
        value = dom.findtext('./os/%s' % param)
        if value is not None:
            conf[param] = value

    if dom.find("./os/bootmenu[@enable='yes']") is not None:
        conf['bootMenuEnable'] = 'true'
    else:
        conf['bootMenuEnable'] = 'false'


def _parse_domain_cpu(dom, conf, arch):
    cpu_topology = dom.find('./cpu/topology')
    if cpu_topology is not None:
        cores = cpu_topology.attrib['cores']
        threads = cpu_topology.attrib['threads']
        sockets = cpu_topology.attrib['sockets']
        conf['smpCoresPerSocket'] = cores
        conf['smpThreadsPerCore'] = threads
        conf['maxVCpus'] = str(int(sockets) * int(cores) * int(threads))

    cpu_tune = dom.find('./cputune')
    if cpu_tune is not None:
        cpu_pinning = {}
        for cpu_pin in dom.findall('./cputune/vcpupin'):
            cpu_pinning[cpu_pin.attrib['vcpu']] = cpu_pin.attrib['cpuset']
        if cpu_pinning:
            conf['cpuPinning'] = cpu_pinning

    cpu_numa = dom.find('./cpu/numa')
    if cpu_numa is not None:
        guest_numa_nodes = []
        for index, cell in enumerate(dom.findall('./cpu/numa/cell')):
            guest_numa_nodes.append({
                'nodeIndex': index,
                'cpus': ','.join(_expand_list(cell.attrib['cpus'])),
                'memory': str(int(cell.attrib['memory']) // 1024),
            })
        conf['guestNumaNodes'] = guest_numa_nodes

    if cpuarch.is_x86(arch):
        _parse_domain_cpu_x86(dom, conf)
    elif cpuarch.is_ppc(arch):
        _parse_domain_cpu_ppc(dom, conf)


def _parse_domain_cpu_x86(dom, conf):
    cpu = dom.find('./cpu')
    if cpu is None:
        return
    features = []
    cpu_mode = cpu.attrib.get('mode')
    if cpu_mode == 'host-passthrough':
        model = 'hostPassthrough'
    elif cpu_mode == 'host-model':
        model = 'hostModel'
    else:
        model = dom.findtext('./cpu/model')
        if model is None:
            model = 'Unknown or Fake'
        features = [
            _parse_domain_cpu_x86_feature(feature)
            for feature in dom.findall('./cpu/features')
        ]

    conf['cpuType'] = ','.join([model] + features)


def _parse_domain_cpu_x86_feature(feature):
    policy = feature.attrib.get('policy', '')
    flag = ''
    if policy == 'require':
        flag = '+'
    elif policy == 'disable':
        flag = '-'
    name = feature.attrib['name']
    return flag + name.replace('sse4.', 'sse4_')


def _parse_domain_cpu_ppc(dom, conf):
    model = dom.findtext('./cpu/model')
    if model is not None:
        conf['cpuType'] = model


def _expand_list(cpuset):
    cpulist = sorted(taskset.cpulist_parse(cpuset))
    return [str(cpu) for cpu in cpulist]


def _parse_domain_input(dom, conf):
    if dom.find("./devices/input[@type='tablet']") is not None:
        conf['tabletEnable'] = 'true'


def make_placeholder_domain_xml(vm):
    return '''<domain type='qemu'>
  <name>{name}</name>
  <uuid>{id}</uuid>
  <memory unit='KiB'>{memory}</memory>
  <os>
    <type arch="{arch}" machine="{machine}">hvm</type>
  </os>
</domain>'''.format(name=escape(vm.name), id=vm.id, memory=vm.mem_size_mb(),
                    arch=vm.arch, machine=_DEFAULT_MACHINES[vm.arch])


def update_sysinfo(dom, osname, osversion, hostserial):
    sys_info = vmxml.find_first(dom, 'sysinfo/system', None)
    if sys_info is None:
        # TODO: log?
        return

    replaceables = {
        'product': ('OS-NAME:', osname),
        'version': ('OS-VERSION:', osversion),
        'serial': ('HOST-SERIAL:', hostserial),
    }

    for entry in vmxml.children(sys_info):
        name = entry.attrib.get('name', None)
        if name not in replaceables:
            continue

        placeholder, value = replaceables[name]
        if entry.text.startswith(placeholder):
            entry.text = value


def make_mdev_element(mdev_uuid):
    hostdev = ET.Element('hostdev')
    hostdev.set('mode', 'subsystem')
    hostdev.set('type', 'mdev')
    hostdev.set('model', 'vfio-pci')
    source = ET.SubElement(hostdev, 'source')
    address = ET.SubElement(source, 'address')
    address.set('uuid', mdev_uuid)
    return hostdev
