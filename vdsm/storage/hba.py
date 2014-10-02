#
# Copyright 2010-2011 Red Hat, Inc.
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

"""
Collect HBA information
"""
import glob
import os
import logging

log = logging.getLogger("Storage.HBA")

ISCSI_INITIATOR_NAME = "/etc/iscsi/initiatorname.iscsi"
INITIATOR_NAME = "InitiatorName"

FC_HOST_MASK = "/sys/class/fc_host/host*"

PORT_NAME = "port_name"
NODE_NAME = "node_name"


def rescan():
    """
    Rescan HBAs connections, updating available devices.

    This operation performs a Loop Initialization Protocol (LIP) and then
    scans the interconnect and causes the SCSI layer to be updated to reflect
    the devices currently on the bus. A LIP is, essentially, a bus reset, and
    will cause device addition and removal.

    Bear in mind that issue_lip is an asynchronous operation. The command may
    complete before the entire scan has completed.

    Note: Must be executed as root.
    TODO: Some drivers do not support this operation.
    """
    log.info("Rescanning HBAs")
    for path in glob.glob(FC_HOST_MASK + '/issue_lip'):
        log.debug("Issuing lip %s", path)
        try:
            with open(path, 'w') as f:
                f.write('1')
        except IOError as e:
            logging.error("Error issuing lip: %s", e)


def getiSCSIInitiators():
    """
    Get iSCSI initiator name from the default location.
    TODO: Check for manual configuration override
    """
    hbas = []
    try:
        with open(ISCSI_INITIATOR_NAME) as f:
            for line in f:
                if line.startswith(INITIATOR_NAME):
                    hba = {'InitiatorName': line.split("=")[1].strip()}
                    hbas.append(hba)
                    break
    except OSError:
        pass

    return hbas


def getModelDesc(fch, host):
    names = ("modelname", "model", "model_name")
    descs = ("modeldesc", "model_description", "model_desc")

    model_name = "Unknown"
    model_desc = "Unknown"
    for name, desc in zip(names, descs):
        name_path = os.path.join(fch, "device", "scsi_host", host, name)
        desc_path = os.path.join(fch, "device", "scsi_host", host, desc)
        try:
            with open(name_path) as name_file:
                model_name = name_file.read().strip()
            with open(desc_path) as desc_file:
                model_desc = desc_file.read().strip()
        except IOError:
            pass   # retry

    return (model_name, model_desc)


def getFCInitiators():
    hbas = []
    fcHosts = glob.glob(FC_HOST_MASK)
    for fch in fcHosts:
        host = os.path.basename(fch)
        # Get FC HBA port name
        portName = os.path.join(fch, PORT_NAME)
        with open(portName) as port_file:
            wwpn = port_file.read().strip().lstrip("0x")
        # Get FC HBA node name
        nodeName = os.path.join(fch, NODE_NAME)
        with open(nodeName) as node_file:
            wwnn = node_file.read().strip().lstrip("0x")
        # Get model name and description
        model = "%s - %s" % getModelDesc(fch, host)
        # Construct FC HBA descriptor
        hbas.append({"wwpn": wwpn, "wwnn": wwnn, "model": model})
    return hbas


def HBAInventory():
    """
    Returns the inventory of the hosts HBAs and their parameters.
    """
    inv = {'iSCSI': getiSCSIInitiators(), 'FC': getFCInitiators()}

    return inv
