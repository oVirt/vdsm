#
# Copyright 2011-2017 Red Hat, Inc.
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

import logging
import os
import os.path
import tempfile
import threading
import time

import libvirt

from vdsm.common import fileutils
from vdsm.common import response
from vdsm.common.compat import pickle
from vdsm import constants
from vdsm import containersconnection
from vdsm import libvirtconnection
from vdsm import utils
from vdsm.virt import vmchannels
from vdsm.virt import vmstatus
from vdsm.virt import vmxml
from vdsm.virt.domain_descriptor import DomainDescriptor
from vdsm.virt.utils import isVdsmImage


def _is_external_vm(dom_xml):
    return (not vmxml.has_channel(dom_xml, vmchannels.LEGACY_DEVICE_NAME) and
            not vmxml.has_vdsm_metadata(dom_xml))


def _is_ignored_vm(dom_xml):
    """
    Return true iff the given VM should never be displayed to users.

    Currently guestfs VMs are ignored and not displayed even as external VMs.
    """
    return vmxml.has_channel(dom_xml, vmchannels.GUESTFS_DEVICE_NAME)


def _list_domains():
    conn = libvirtconnection.get()
    domains = []
    for dom_obj in conn.listAllDomains():
        dom_uuid = 'unknown'
        try:
            dom_uuid = dom_obj.UUIDString()
            logging.debug("Found domain %s", dom_uuid)
            dom_xml = dom_obj.XMLDesc(0)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                logging.exception("domain %s is dead", dom_uuid)
            else:
                raise
        else:
            if _is_ignored_vm(dom_xml):
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
    params['smp'] = dom.get_number_of_cpus()
    params['memSize'] = dom.get_memory_size()
    return params


class File(object):
    """
    "pickle" for vm state.
    """

    EXTENSION = ".recovery"

    _log = logging.getLogger("virt.recovery.file")

    def __init__(self, vmid):
        self._vmid = vmid
        self._name = '%s%s' % (vmid, self.EXTENSION)
        self._path = os.path.join(constants.P_VDSM_RUN, self._name)
        self._lock = threading.Lock()

    @property
    def vmid(self):
        return self._vmid

    @property
    def name(self):
        return self._name

    def cleanup(self):
        with self._lock:
            fileutils.rm_file(self._path)
            self._path = None

    def save(self, vm):
        data = self._collect(vm)
        with self._lock:
            if self._path is None:
                self._log.debug('save after cleanup')
            else:
                self._dump(data)

    def load(self, cif, dom_xml=None):
        self._log.debug("recovery: trying with VM %s", self._vmid)
        try:
            with open(self._path) as src:
                params = pickle.load(src)
            self._set_elapsed_time(params)
            self._update_domain_xml(params, dom_xml)
            res = cif.createVm(params, vmRecover=True)
        except Exception:
            self._log.exception("Error recovering VM: %s", self._vmid)
            return False
        else:
            if response.is_error(res):
                return False
            return True

    def _dump(self, data):
        with tempfile.NamedTemporaryFile(
            dir=constants.P_VDSM_RUN,
            delete=False
        ) as f:
            pickle.dump(data, f)

        os.rename(f.name, self._path)

    def _collect(self, vm):
        data = vm.status()
        data['startTime'] = vm.start_time
        if vm.lastStatus != vmstatus.DOWN:
            guestInfo = vm.guestAgent.getGuestInfo()
            data['username'] = guestInfo['username']
            data['guestIPs'] = guestInfo['guestIPs']
            data['guestFQDN'] = guestInfo['guestFQDN']
        else:
            data['username'] = ""
            data['guestIPs'] = ""
            data['guestFQDN'] = ""
        if 'sysprepInf' in data:
            del data['sysprepInf']
            if 'floppy' in data:
                del data['floppy']
        for drive in data.get('drives', []):
            for d in vm.getDiskDevices():
                if isVdsmImage(d) and drive.get('volumeID') == d.volumeID:
                    drive['truesize'] = str(d.truesize)
                    drive['apparentsize'] = str(d.apparentsize)

        data['_blockJobs'] = utils.picklecopy(
            vm.conf.get('_blockJobs', {}))

        return data

    def _set_elapsed_time(self, params):
        now = time.time()
        pt = float(params.pop('startTime', now))
        params['elapsedTimeOffset'] = now - pt
        return params

    def _update_domain_xml(self, params, dom_xml):
        if dom_xml is not None and 'xml' in params:
            params['xml'] = dom_xml
            self._log.info("Recovered XML from libvirt: %s", dom_xml)
        return params


def all_domains(cif):
    doms = _list_domains() + containersconnection.recovery()
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
    for vm_id in cif.get_unknown_vm_ids():
        try:
            dom_obj = conn.lookupByUUIDString(vm_id)
            dom_xml = dom_obj.XMLDesc(0)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                logging.debug("External domain %s not found", vm_id)
                continue
            else:
                raise
        if _is_ignored_vm(dom_xml):
            continue
        logging.debug("Recovering external domain: %s", vm_id)
        if _recover_domain(cif, vm_id, dom_xml, True):
            cif.log.info("Recovered new external domain: %s", vm_id)
        else:
            cif.log.info("Failed to recover new external domain: %s", vm_id)
