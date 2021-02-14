#
# Copyright 2010-2017 Red Hat, Inc.
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

from __future__ import absolute_import

import glob
import logging
import os

from vdsm import constants
from vdsm import utils
from vdsm.common import commands
from vdsm.common import supervdsm
from vdsm.common.compat import subprocess
from vdsm.config import config
from vdsm.storage import misc

log = logging.getLogger("storage.hba")

ISCSI_INITIATOR_NAME = "/etc/iscsi/initiatorname.iscsi"
INITIATOR_NAME = "InitiatorName"

FC_HOST_MASK = "/sys/class/fc_host/host*"

PORT_NAME = "port_name"
NODE_NAME = "node_name"


class Error(Exception):
    """ hba operation failed """


@misc.samplingmethod
def rescan():
    """
    Rescan HBAs discovering new devices.
    """
    log.info("Scanning FC devices")
    try:
        with utils.stopwatch(
                "Scanning FC devices", level=logging.INFO, log=log):
            supervdsm.getProxy().hbaRescan()
    except Error as e:
        log.error("Scanning FC devices failed: %s", e)


def _rescan():
    """
    Called from supervdsm to perform rescan as root.
    """
    timeout = config.getint('irs', 'scsi_rescan_maximal_timeout')

    p = commands.start(
        [constants.EXT_FC_SCAN],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # TODO: Raising a timeout allows a new scan to start before this scan
        # terminates. The new scan is likely to be blocked until this scan
        # terminates.
        commands.wait_async(p)
        raise Error("Timeout scanning (pid=%s)" % p.pid)

    if p.returncode != 0:
        raise Error("Scan failed with rc=%s out=%r err=%r"
                    % (p.returncode, out, err))


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
    except EnvironmentError:
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
