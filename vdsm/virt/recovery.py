#
# Copyright 2011-2015 Red Hat, Inc.
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

import os
import os.path
import time

import libvirt

from vdsm.compat import pickle
from vdsm import constants
from vdsm import response
from vdsm import utils

from .vm import getVDSMDomains


def all_vms(cif):
    # Recover stage 1: domains from libvirt
    _all_vms_from_libvirt(cif)

    # Recover stage 2: domains from recovery files
    # we do this to safely handle VMs which disappeared
    # from the host while VDSM was down/restarting
    _all_vms_from_files(cif)


def _all_vms_from_libvirt(cif):
    doms = getVDSMDomains()
    num_doms = len(doms)
    for idx, v in enumerate(doms):
        vm_id = v.UUIDString()
        if _vm_from_file(cif, vm_id):
            cif.log.info(
                'recovery [1:%d/%d]: recovered domain %s from libvirt',
                idx+1, num_doms, vm_id)
        else:
            cif.log.info(
                'recovery [1:%d/%d]: loose domain %s found, killing it.',
                idx+1, num_doms, vm_id)
            try:
                v.destroy()
            except libvirt.libvirtError:
                cif.log.exception(
                    'recovery [1:%d/%d]: failed to kill loose domain %s',
                    idx+1, num_doms, vm_id)


def _all_vms_from_files(cif):
    rec_vms = _find_vdsm_vms_from_files(cif)
    num_rec_vms = len(rec_vms)
    if rec_vms:
        cif.log.warning(
            'recovery: found %i VMs from recovery files not'
            ' reported by libvirt. This should not happen!'
            ' Will try to recover them.', num_rec_vms)

    for idx, vm_id in enumerate(rec_vms):
        if _vm_from_file(cif, vm_id):
            cif.log.info(
                'recovery [2:%d/%d]: recovered domain %s'
                ' from data file', idx+1, num_rec_vms, vm_id)
        else:
            cif.log.warning(
                'recovery [2:%d/%d]: VM %s failed to recover from data'
                ' file, reported as Down', idx+1, num_rec_vms, vm_id)


def _find_vdsm_vms_from_files(cif):
    vms = []
    for f in os.listdir(constants.P_VDSM_RUN):
        vm_id, fileType = os.path.splitext(f)
        if fileType == ".recovery":
            if vm_id not in cif.vmContainer:
                vms.append(vm_id)
    return vms


def _vm_from_file(cif, vmid):
    try:
        recovery_file = constants.P_VDSM_RUN + vmid + ".recovery"
        params = pickle.load(file(recovery_file))
        now = time.time()
        pt = float(params.pop('startTime', now))
        params['elapsedTimeOffset'] = now - pt
        cif.log.debug("recovery: trying with domain %s", vmid)
        if response.is_error(cif.createVm(params, vmRecover=True)):
            return None
    except:
        cif.log.debug("Error recovering VM", exc_info=True)
        return None
    else:
        return recovery_file


def clean_vm_files(cif):
    for f in os.listdir(constants.P_VDSM_RUN):
        try:
            vmId, fileType = f.split(".", 1)
        except ValueError:
            # If file is missing type extention - ignore it
            pass
        else:
            if fileType == "recovery" and vmId not in cif.vmContainer:
                cif.log.debug("cleaning old file " + f)
                utils.rmFile(constants.P_VDSM_RUN + f)
