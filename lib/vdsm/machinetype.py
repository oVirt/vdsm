# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import itertools
import libvirt
import logging
import xml.etree.ElementTree as ET

from vdsm.common import cache
from vdsm.common import cpuarch
from vdsm.common import libvirtconnection
from vdsm.common.config import config
from vdsm.validatehost import is_valid_virt_host


class _CpuMode:
    HOST_MODEL = 'host-model'
    CUSTOM = 'custom'


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


def _get_domain_capabilities(conn, arch):
    """
    Read libvirt domain capabilities and parse them.

    Arguments:
        conn(libvirtconnection) - libvirt connection object for the
                                  hypervisor to be queried for CPU models.
        arch(string) - CPU architecture, one of cpuarch constants

    Returns:
        ET.Element instance of dom capabilities or None
    """
    if config.getboolean('vars', 'fake_kvm_support'):
        virt_type = 'qemu'
    else:
        virt_type = 'kvm'
    try:
        domcaps = conn.getDomainCapabilities(None, arch, None, virt_type, 0)
    except libvirt.libvirtError:
        logging.exception('Error while getting domain capabilities')
        return None

    return ET.fromstring(domcaps)


def domain_cpu_models(conn, arch, cpu_mode):
    """
    Parse libvirt domain capabilities to get cpu models known by the
    hypervisor along with usability status.

    Arguments:
        conn(libvirtconnection) - libvirt connection object for the
                                  hypervisor to be queried for CPU models.
        arch(string) - CPU architecture, one of cpuarch constants
        cpu_mode(string) - CPU mode, one of _CpuMode constants;
                           _CpuMode.HOST_MODEL is used on POWER,
                           _CpuMode.CUSTOM on other architectures

    Returns:
        {str: str} - mapping where key is CPU model and value is one
                     of 'yes', 'no' or 'unknown', showing whether
                     the particular model can be used on this hypervisor.

    Example:
        {'z13' : 'unknown', 'zEC12': 'no', 'z196': 'yes'}
    """
    xmldomcaps = _get_domain_capabilities(conn, arch)
    if xmldomcaps is None:
        logging.error('Error while getting CPU models: '
                      'no domain capabilities found')
        return {}

    cpucaps = xmldomcaps.find('cpu')
    if cpucaps is None:
        logging.error('Error while getting CPU models: '
                      'no domain CPU capabilities found')
        return {}

    dom_models = dict()
    for mode in cpucaps.findall('mode'):
        if mode.get('name') == cpu_mode and mode.get('supported') == 'yes':
            for model in mode.findall('model'):
                if cpu_mode == _CpuMode.CUSTOM:
                    usable = model.get('usable')
                else:
                    usable = 'yes'
                if model.text:
                    dom_models[model.text] = usable
    logging.debug('Supported CPU models: %s', dom_models)

    return dom_models


@cache.memoized
def compatible_cpu_models():
    """
    Compare qemu's CPU models to models the host is capable of emulating.

    Returns:
        A list of strings indicating compatible CPU models prefixed
        with 'model_'.

    Example:
        ['model_Haswell-noTSX', 'model_Nehalem', 'model_Conroe',
        'model_coreduo', 'model_core2duo', 'model_Penryn',
        'model_IvyBridge', 'model_Westmere', 'model_n270', 'model_SandyBridge']
    """
    c = libvirtconnection.get()
    arch = cpuarch.real()
    cpu_mode = _CpuMode.HOST_MODEL if cpuarch.is_ppc(arch) else _CpuMode.CUSTOM
    all_models = domain_cpu_models(c, arch, cpu_mode)
    compatible_models = [model for (model, usable)
                         in all_models.items()
                         if usable == 'yes']
    logging.debug(Compatible CPU models: %s', compatible_models)
    # Current QEMU doesn't report POWER compatibility modes, so we
    # must add them ourselves.
    if cpuarch.is_ppc(arch) and \
       'POWER9' in compatible_models and \
       'POWER8' not in compatible_models:
        compatible_models.append('POWER8')
    if cpuarch.is_arm(arch) and is_valid_virt_host():
        compatible_models.append('virt_aarch64')
    return list(set(["model_" + model for model in compatible_models]))


@cache.memoized
def cpu_features():
    """
    Read CPU features from dom capabilities.

    Returns:
        A list of strings indicating CPU features.
    """
    c = libvirtconnection.get()
    arch = cpuarch.real()
    xmldomcaps = _get_domain_capabilities(c, arch)
    if xmldomcaps is None:
        logging.error('Error while getting CPU features: '
                      'no domain capabilities found')
        return []

    cpucaps = xmldomcaps.find('cpu')
    if cpucaps is None:
        logging.error('Error while getting CPU features: '
                      'no domain CPU capabilities found')
        return []

    features = []
    for mode in cpucaps.findall('mode'):
        if mode.get('name') == _CpuMode.HOST_MODEL:
            for feature in mode.findall('feature'):
                if feature.get('policy') == 'require':
                    features.append(feature.get('name'))
    logging.debug('CPU features: %s', features)

    return features


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
    if arch_tag is None:
        logging.error('Error while looking for architecture '
                      '"%s" in libvirt capabilities', arch)
        return []

    return _emulated_machines_from_caps_node(arch_tag)


def _emulated_machines_from_caps_domain(arch, caps):
    domain_tag = caps.find(
        './/guest/arch[@name="%s"]/domain[@type="kvm"]' % arch)
    if domain_tag is None:
        logging.error('Error while looking for kvm domain (%s) '
                      'libvirt capabilities', arch)
        return []

    return _emulated_machines_from_caps_node(domain_tag)


def _get_libvirt_caps():
    conn = libvirtconnection.get()
    return conn.getCapabilities()
