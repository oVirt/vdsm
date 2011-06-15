#
# Copyright 2010 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
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

def getiSCSIInitiators():
    """
    Get iSCSI initiator name from the default location.
    TODO: Check for manual configuration override
    """
    hbas = []
    try:
        with file(ISCSI_INITIATOR_NAME) as f:
            for line in f:
                if line.startswith(INITIATOR_NAME):
                    hba = {'InitiatorName':line.split("=")[1].strip()}
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
            model_name = file(name_path).read().strip()
            model_desc = file(desc_path).read().strip()
        except IOError:
            pass #retry

    return (model_name, model_desc)


def getFCInitiators():
    hbas = []
    fcHosts = glob.glob(FC_HOST_MASK)
    for fch in fcHosts:
        host = os.path.basename(fch)
        # Get FC HBA port name
        portName = os.path.join(fch, PORT_NAME)
        wwpn = file(portName).read().strip().lstrip("0x")
        # Get FC HBA node name
        nodeName = os.path.join(fch, NODE_NAME)
        wwnn = file(nodeName).read().strip().lstrip("0x")
        # Get model name and description
        model = "%s - %s" % getModelDesc(fch, host)
        # Construct FC HBA descriptor
        hbas.append({"wwpn":wwpn, "wwnn":wwnn, "model":model})
    return hbas


def HBAInventory():
    """
    Returns the inventory of the hosts HBAs and their parameters.
    """
    inv = {'iSCSI':getiSCSIInitiators(), 'FC':getFCInitiators()}

    return inv
