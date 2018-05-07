#
# Copyright 2016-2017 Red Hat, Inc.
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

import itertools
import logging
import six
import xml.etree.ElementTree as ET

import libvirt

from vdsm.common import cache
from vdsm.common import cpuarch
from vdsm.common import libvirtconnection

CPU_MAP_FILE = '/usr/share/libvirt/cpu_map.xml'


@cache.memoized
def emulated_machines(arch, capabilities=None):
    """
    Parse libvirt capabilties to obtain supported emulated machines on the
    host.

    Arguments:

    arch            Target emulation architecture.

    capabilities    Libvirt capabilities (virsh -r capabilities) string.

    Returns:
        A list of strings indicating the supported emulated machine types.

    Example:
        ['pc-i440fx-rhel7.1.0', 'rhel6.3.0', 'pc-q35-rhel7.2.0',
        'pc-i440fx-rhel7.0.0', 'rhel6.1.0', 'rhel6.6.0', 'rhel6.2.0',
        'pc', 'pc-q35-rhel7.0.0', 'pc-q35-rhel7.1.0', 'q35',
        'pc-i440fx-rhel7.2.0', 'rhel6.4.0', 'rhel6.0.0', 'rhel6.5.0']

    """
    if capabilities is None:
        capabilities = _get_libvirt_caps()
    caps = ET.fromstring(capabilities)

    # machine list from domain can legally be empty
    # (e.g. only qemu-kvm installed)
    # in that case it is fine to use machines list from arch
    return (_emulated_machines_from_caps_domain(arch, caps) or
            _emulated_machines_from_caps_arch(arch, caps))


def cpu_models(capfile=CPU_MAP_FILE, arch=None):
    """
    Parse libvirt capabilties to obtain supported cpu models on the host.

    Arguments:

    capfile     Path to file in libvirt's CPU_MAP.xml format.

    arch        Architecture of the CPUs. Defaults to host's real architecture.

    Returns:
        {str: str} - mapping where key is CPU model and value is CPU vendor.

    Example:
        {'POWER7': 'IBM', 'POWER6': 'IBM', 'POWERPC_e6500': 'Freescale',
        'POWERPC_e5500': 'Freescale', 'POWER8': 'IBM'}
    """
    if arch is None:
        arch = cpuarch.real()

    arch_element = _caps_arch_element(capfile, arch)

    if not arch_element:
        logging.error('Error while getting all CPU models: the host '
                      'architecture is not supported', exc_info=True)
        return {}

    all_models = dict()

    for m in arch_element.findall('model'):
        element = m.find('vendor')
        if element is not None:
            vendor = element.get('name')
        else:
            element = m.find('model')
            if element is None:
                vendor = None
            else:
                elementName = element.get('name')
                vendor = all_models.get(elementName, None)
        all_models[m.get('name')] = vendor
    return all_models


def domain_cpu_models(conn, arch):
    """
    Parse libvirt domain capabilities to get cpu models known by the
    hypervisor along with usability status.

    Arguments:
        conn(libvirtconnection) - libvirt connection object for the
                                  hypervisor to be queried for CPU models.

    Returns:
        {str: str} - mapping where key is CPU model and value is one
                     of 'yes', 'no' or 'unknown', showing whether
                     the particular model can be used on this hypervisor.

    Example:
        {'z13' : 'unknown', 'zEC12': 'no', 'z196': 'yes'}
   """
    domcaps = conn.getDomainCapabilities(None, arch, None, None, 0)
    if not domcaps:
        logging.error('Error while getting CPU models: '
                      'no domain capabilities found')
        return {}

    xmldomcaps = ET.fromstring(domcaps)
    cpucaps = xmldomcaps.find('cpu')
    if cpucaps is None:
        logging.error('Error while getting CPU models: '
                      'no domain CPU capabilities found')
        return {}

    dom_models = dict()
    for mode in cpucaps.findall('mode'):
        if mode.get('name') == 'custom' and mode.get('supported') == 'yes':
            for models in mode.findall('model'):
                dom_models[models.text] = models.get('usable')

    return dom_models


@cache.memoized
def compatible_cpu_models():
    """
    Compare qemu's CPU models to models the host is capable of emulating.
    Due to historic reasons, this comparison takes into account the CPU vendor.

    Returns:
        A list of strings indicating compatible CPU models prefixed
        with 'model_'.

    Example:
        ['model_Haswell-noTSX', 'model_Nehalem', 'model_Conroe',
        'model_coreduo', 'model_core2duo', 'model_Penryn',
        'model_IvyBridge', 'model_Westmere', 'model_n270', 'model_SandyBridge']
    """
    def compatible(model, vendor):
        if not vendor:
            return False

        mode_xml = ''
        # POWER CPUs are special case because we run them using versioned
        # compat mode (aka host-model). Libvirt's compareCPU call uses the
        # selected mode - we have to be sure to tell it to compare CPU
        # capabilities based on the compat features, not the CPU itself.
        if cpuarch.is_ppc(cpuarch.real()):
            mode_xml = " mode='host-model'"
            model = model.lower()

        xml = '<cpu match="minimum"%s><model>%s</model>' \
              '<vendor>%s</vendor></cpu>' % (mode_xml, model, vendor)
        try:
            return c.compareCPU(xml, 0) in (libvirt.VIR_CPU_COMPARE_SUPERSET,
                                            libvirt.VIR_CPU_COMPARE_IDENTICAL)
        except libvirt.libvirtError as e:
            # hack around libvirt BZ#795836
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                return False
            raise

    compatible_models = []
    c = libvirtconnection.get()
    arch = cpuarch.real()
    if arch == cpuarch.S390X:
        # s390x uses libvirt domain caps for CPU model reporting
        all_models = domain_cpu_models(c, arch)
        compatible_models = [model for (model, usable)
                             in six.iteritems(all_models)
                             if usable == 'yes']
    else:
        all_models = cpu_models()
        compatible_models = [model for (model, vendor)
                             in six.iteritems(all_models)
                             if compatible(model, vendor)]

    return list(set(["model_" + model for model in compatible_models]))


def _caps_arch_element(capfile, arch):
    with open(capfile) as xml:
        cpu_map = ET.fromstring(xml.read())

    # In libvirt CPU map XML, both x86_64 and x86 are
    # the same architecture, so in order to find all
    # the CPU models for this architecture, 'x86'
    # must be used
    if cpuarch.is_x86(arch):
        arch = 'x86'

    if cpuarch.is_ppc(arch):
        arch = 'ppc64'

    arch_element = None

    arch_elements = cpu_map.findall('arch')

    if arch_elements:
        for element in arch_elements:
            if element.get('name') == arch:
                arch_element = element

    return arch_element


def _emulated_machines_from_caps_node(node):
    # We have to make sure to inspect 'canonical' attribute where
    # libvirt puts the real machine name. Relevant bug:
    # https://bugzilla.redhat.com/show_bug.cgi?id=1229666
    return list(set((itertools.chain.from_iterable(
        (
            (m.text, m.get('canonical'))
            if m.get('canonical') else
            (m.text,)
        )
        for m in node.iterfind('machine')))))


def _emulated_machines_from_caps_arch(arch, caps):
    arch_tag = caps.find('.//guest/arch[@name="%s"]' % arch)
    if not arch_tag:
        logging.error('Error while looking for architecture '
                      '"%s" in libvirt capabilities', arch)
        return []

    return _emulated_machines_from_caps_node(arch_tag)


def _emulated_machines_from_caps_domain(arch, caps):
    domain_tag = caps.find(
        './/guest/arch[@name="%s"]/domain[@type="kvm"]' % arch)
    if not domain_tag:
        logging.error('Error while looking for kvm domain (%s) '
                      'libvirt capabilities', arch)
        return []

    return _emulated_machines_from_caps_node(domain_tag)


def _get_libvirt_caps():
    conn = libvirtconnection.get()
    return conn.getCapabilities()
