#
# Copyright 2016 Red Hat, Inc.
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
import xml.etree.ElementTree as ET

import libvirt

from . import cpuarch
from . import libvirtconnection
from . import utils

CPU_MAP_FILE = '/usr/share/libvirt/cpu_map.xml'


def _get_emulated_machines_from_node(node):
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


def _get_emulated_machines_from_arch(arch, caps):
    arch_tag = caps.find('.//guest/arch[@name="%s"]' % arch)
    if not arch_tag:
        logging.error('Error while looking for architecture '
                      '"%s" in libvirt capabilities', arch)
        return []

    return _get_emulated_machines_from_node(arch_tag)


def _get_emulated_machines_from_domain(arch, caps):
    domain_tag = caps.find(
        './/guest/arch[@name="%s"]/domain[@type="kvm"]' % arch)
    if not domain_tag:
        logging.error('Error while looking for kvm domain (%s) '
                      'libvirt capabilities', arch)
        return []

    return _get_emulated_machines_from_node(domain_tag)


@utils.memoized
def emulated_machines(arch, capabilities=None):
    if capabilities is None:
        capabilities = _get_libvirt_caps()
    caps = ET.fromstring(capabilities)

    # machine list from domain can legally be empty
    # (e.g. only qemu-kvm installed)
    # in that case it is fine to use machines list from arch
    return (_get_emulated_machines_from_domain(arch, caps) or
            _get_emulated_machines_from_arch(arch, caps))


def cpu_models(capfile=CPU_MAP_FILE, arch=None):
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


@utils.memoized
def compatible_cpu_models():
    c = libvirtconnection.get()
    all_models = cpu_models()

    def compatible(model, vendor):
        if not vendor:
            return False
        xml = '<cpu match="minimum"><model>%s</model>' \
              '<vendor>%s</vendor></cpu>' % (model, vendor)
        try:
            return c.compareCPU(xml, 0) in (libvirt.VIR_CPU_COMPARE_SUPERSET,
                                            libvirt.VIR_CPU_COMPARE_IDENTICAL)
        except libvirt.libvirtError as e:
            # hack around libvirt BZ#795836
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                return False
            raise

    return ['model_' + model for (model, vendor)
            in all_models.iteritems() if compatible(model, vendor)]


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


def _get_libvirt_caps():
    conn = libvirtconnection.get()
    return conn.getCapabilities()
