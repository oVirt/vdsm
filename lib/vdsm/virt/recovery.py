#
# Copyright 2011-2021 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import logging

import libvirt

from vdsm.common import libvirtconnection
from vdsm.common import response
from vdsm.virt import vmchannels
from vdsm.virt import vmstatus
from vdsm.virt import vmxml
from vdsm.virt.domain_descriptor import DomainDescriptor


def _is_external_vm(dom_xml):
    return (not vmxml.has_channel(dom_xml, vmchannels.LEGACY_DEVICE_NAME) and
            not vmxml.has_vdsm_metadata(dom_xml))


def _is_ignored_vm(dom_uuid, dom_obj, dom_xml):
    """
    Return true iff the given VM should never be displayed to users.

    Currently all guestfs VMs and external VMs in DOWN status are ignored.
    """
    if vmxml.has_channel(dom_xml, vmchannels.GUESTFS_DEVICE_NAME):
        return True
    if _is_external_vm(dom_xml):
        try:
            state, reason = dom_obj.state(0)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return True
            else:
                logging.warning("Can't get status of external VM %s: %s",
                                dom_uuid, e)
        if state in vmstatus.LIBVIRT_DOWN_STATES:
            return True
    return False


def _list_domains():
    conn = libvirtconnection.get()
    domains = []
    for dom_obj in conn.listAllDomains():
        dom_uuid = 'unknown'
        try:
            dom_uuid = dom_obj.UUIDString()
            logging.debug("Found domain %s", dom_uuid)
            dom_xml = dom_obj.XMLDesc()
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                logging.exception("domain %s is dead", dom_uuid)
            else:
                raise
        else:
            if _is_ignored_vm(dom_uuid, dom_obj, dom_xml):
                continue
            domains.append((dom_obj, dom_xml, _is_external_vm(dom_xml),))
    return domains


def _recover_domain(cif, vm_id, dom_xml, external):
    external_str = " (external)" if external else ""
    cif.log.debug("recovery: trying with VM%s %s", external_str, vm_id)
    try:
        res = cif.createVm(_recovery_params(vm_id, dom_xml, external),
                           vmRecover=True)
    except Exception:
        cif.log.exception("Error recovering VM%s: %s", external_str, vm_id)
        return False
    if response.is_error(res):
        cif.log.info("Failed to recover VM%s: %s (%s)",
                     external_str, vm_id, res)
        return False
    cif.log.info("VM recovered: %s", vm_id)
    return True


def _recovery_params(vm_id, dom_xml, external):
    params = {
        'xml': dom_xml,
        'external': external,
    }
    dom = DomainDescriptor(dom_xml)
    params['vmType'] = dom.vm_type()
    params['vmName'] = dom.name
    params['vmId'] = dom.id
    return params


def all_domains(cif):
    doms = _list_domains()
    num_doms = len(doms)
    for idx, (dom_obj, dom_xml, external) in enumerate(doms):
        vm_id = dom_obj.UUIDString()
        if _recover_domain(cif, vm_id, dom_xml, external):
            cif.log.info(
                'recovery [1:%d/%d]: recovered domain %s',
                idx + 1, num_doms, vm_id)
        elif external:
            cif.log.info("Failed to recover external domain: %s" % (vm_id,))
        else:
            cif.log.info(
                'recovery [1:%d/%d]: loose domain %s found, killing it.',
                idx + 1, num_doms, vm_id)
            try:
                dom_obj.destroy()
            except libvirt.libvirtError:
                cif.log.exception(
                    'recovery [1:%d/%d]: failed to kill loose domain %s',
                    idx + 1, num_doms, vm_id)


def lookup_external_vms(cif):
    conn = libvirtconnection.get()
    for vm_id in cif.pop_unknown_vm_ids():
        try:
            dom_obj = conn.lookupByUUIDString(vm_id)
            dom_xml = dom_obj.XMLDesc()
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                logging.debug("External domain %s not found", vm_id)
                continue
            else:
                logging.exception("Failed to retrieve external VM: %s", vm_id)
                cif.add_unknown_vm_id(vm_id)
                continue
        if _is_ignored_vm(vm_id, dom_obj, dom_xml):
            continue
        logging.debug("Recovering external domain: %s", vm_id)
        if _recover_domain(cif, vm_id, dom_xml, True):
            cif.log.info("Recovered new external domain: %s", vm_id)
        else:
            cif.log.info("Failed to recover new external domain: %s", vm_id)
