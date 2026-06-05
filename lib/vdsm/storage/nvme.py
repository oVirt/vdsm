# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
NVMe-oF service module. Provides helper functions to interact with nvme-cli
facility for NVMe over Fabrics (NVMe-oF) connections.
"""

import glob
import logging
import os
import re

from vdsm.common import cmdutils
from vdsm.common import commands

_NVME = cmdutils.CommandPath("nvme",
                             "/usr/sbin/nvme",
                             "/sbin/nvme")

# Sysfs paths for NVMe-oF connected controllers
SYS_NVME = "/sys/class/nvme"
SYS_NVME_SUBSYS = "/sys/class/nvme-subsystem"

log = logging.getLogger("storage.nvme")


class NvmeError(Exception):
    pass


class NvmeConnectionError(NvmeError):
    pass


class NvmeAuthenticationError(NvmeError):
    pass


class NvmeDisconnectionError(NvmeError):
    pass


class NvmeControllerLookupError(NvmeError):
    pass


def connect(nqn, traddr, trsvcid="4420", transport="tcp",
            host_nqn=None, dhchap_key=None):
    """
    Connect to an NVMe-oF target.

    Arguments:
        nqn (str): NVMe Qualified Name of the target
        traddr (str): Transport address (IP or hostname)
        trsvcid (str): Transport service port (default "4420")
        transport (str): Transport type (default "tcp")
        host_nqn (str): Initiator NQN (optional)
        dhchap_key (str): DH-HMAC-CHAP key (optional)

    Raises:
        NvmeConnectionError if connection fails
    """
    cmd = [_NVME.cmd, "connect",
           "-n", nqn,
           "-t", transport,
           "-a", traddr,
           "-s", trsvcid]
    if host_nqn:
        cmd.extend(["-w", host_nqn])
    if dhchap_key:
        cmd.extend(["-k", dhchap_key])

    log.info("Connecting to NVMe-oF target %s at %s:%s (transport=%s)",
             nqn, traddr, trsvcid, transport)
    try:
        commands.run(cmd)
    except cmdutils.Error as e:
        log.error("NVMe connect failed: %s", e)
        _raise_connect_error(e)


def disconnect(nqn):
    """
    Disconnect from an NVMe-oF target by NQN.

    Arguments:
        nqn (str): NVMe Qualified Name to disconnect

    Raises:
        NvmeDisconnectionError if disconnection fails
    """
    cmd = [_NVME.cmd, "disconnect", "-n", nqn]
    log.info("Disconnecting from NVMe-oF target %s", nqn)
    try:
        commands.run(cmd)
    except cmdutils.Error as e:
        log.error("NVMe disconnect failed: %s", e)
        raise NvmeDisconnectionError(
            "Failed to disconnect from {}: {}".format(nqn, e))


def disconnect_all():
    """
    Disconnect all NVMe-oF connections.
    """
    cmd = [_NVME.cmd, "disconnect-all"]
    log.info("Disconnecting all NVMe-oF connections")
    try:
        commands.run(cmd)
    except cmdutils.Error as e:
        log.error("NVMe disconnect-all failed: %s", e)
        raise NvmeDisconnectionError(
            "Failed to disconnect all: {}".format(e))


def list_controllers():
    """
    List all connected NVMe controllers.

    Returns:
        list of dict with controller info parsed from `nvme list`
    """
    cmd = [_NVME.cmd, "list", "--output-format=json"]
    try:
        out = commands.run(cmd)
    except cmdutils.Error as e:
        log.error("NVMe list failed: %s", e)
        return []

    import json
    try:
        data = json.loads(out.decode("utf-8"))
    except (ValueError, KeyError) as e:
        log.error("Failed to parse nvme list output: %s", e)
        return []

    controllers = []
    for entry in data.get("Devices", []):
        controllers.append({
            "device": entry.get("DevicePath", ""),
            "firmware": entry.get("Firmware", ""),
            "model": entry.get("ModelNumber", ""),
            "serial": entry.get("SerialNumber", ""),
            "used_bytes": entry.get("UsedBytes", 0),
            "max_lba": entry.get("NamespaceSize", 0),
            "physical_size": entry.get("PhysicalSize", 0),
            "sector_size": entry.get("SectorSize", 0),
        })
    return controllers


def list_subsystems(device=None):
    """
    List NVMe subsystems. If device is specified, list subsystems for that
    device.

    Returns:
        dict with subsystem information
    """
    cmd = [_NVME.cmd, "list-subsys"]
    if device:
        cmd.append(device)

    try:
        out = commands.run(cmd)
    except cmdutils.Error as e:
        log.error("NVMe list-subsys failed: %s", e)
        return {}

    return _parse_list_subsys_output(out.decode("utf-8"))


def get_host_nqn():
    """
    Read the host NQN from /etc/nvme/hostnqn.

    Returns:
        str or None if not found
    """
    try:
        with open("/etc/nvme/hostnqn", "r") as f:
            return f.read().strip()
    except (OSError, IOError):
        log.debug("Host NQN file not found")
        return None


def get_connected_nqns():
    """
    Get list of NQNs currently connected via NVMe-oF.

    Returns:
        list of (nqn, traddr, trsvcid, transport) tuples
    """
    connected = []
    subsys_pattern = os.path.join(SYS_NVME_SUBSYS, "nvme-subsys*")
    for subsys_dir in glob.glob(subsys_pattern):
        subsys_name = os.path.basename(subsys_dir)
        nqn = _read_sysfs_attr(os.path.join(subsys_dir, "subsysnqn"))
        if not nqn:
            continue
        controller_dir = os.path.join(
            SYS_NVME, subsys_name.replace("nvme-subsys", "nvme"))
        if not os.path.isdir(controller_dir):
            continue
        address = _read_sysfs_attr(os.path.join(controller_dir, "address"))
        transport = _read_sysfs_attr(
            os.path.join(controller_dir, "transport"))
        traddr, trsvcid = _parse_address(address) if address else (None, None)
        if nqn and traddr:
            connected.append((nqn, traddr, trsvcid, transport or "tcp"))
    return connected


def is_connected(nqn):
    """
    Check if a specific NQN is connected.

    Returns:
        bool
    """
    for connected_nqn, _, _, _ in get_connected_nqns():
        if connected_nqn == nqn:
            return True
    return False


def get_connection_info(device):
    """
    Get NVMe-oF connection info for a given device name (e.g., nvme0).

    Arguments:
        device (str): Device name (e.g., "nvme0")

    Returns:
        dict with keys: nqn, traddr, trsvcid, transport or None if not found
    """
    subsys = _device_to_subsys(device)
    if not subsys:
        return None

    nqn = _read_sysfs_attr(os.path.join(subsys, "subsysnqn"))
    controller = os.path.join(SYS_NVME, device)
    if not os.path.isdir(controller):
        return None

    address = _read_sysfs_attr(os.path.join(controller, "address"))
    transport = _read_sysfs_attr(os.path.join(controller, "transport"))
    traddr, trsvcid = _parse_address(address) if address else (None, None)

    if not nqn or not traddr:
        return None

    return {
        "nqn": nqn,
        "traddr": traddr,
        "trsvcid": trsvcid or "4420",
        "transport": transport or "tcp",
    }


def dev_is_nvme(dev):
    """
    Check if a device (slave name) is an NVMe device.

    Arguments:
        dev (str): Device name (e.g., "nvme0n1")

    Returns:
        bool
    """
    device_path = os.path.realpath(
        os.path.join("/sys", "block", dev, "device"))
    if not os.path.exists(device_path):
        return False
    return "nvme" in device_path


def get_session_nqn(dev):
    """
    Get the NQN associated with an NVMe device.

    Arguments:
        dev (str): Device name (e.g., "nvme0n1")

    Returns:
        str or None
    """
    subsys = _device_to_subsys(dev)
    if not subsys:
        return None
    return _read_sysfs_attr(os.path.join(subsys, "subsysnqn"))


def get_session_address(dev):
    """
    Get the transport address for an NVMe device.

    Arguments:
        dev (str): Device name (e.g., "nvme0n1")

    Returns:
        str (traddr) or None
    """
    controller = _device_to_controller(dev)
    if not controller:
        return None
    address = _read_sysfs_attr(os.path.join(controller, "address"))
    if not address:
        return None
    traddr, _ = _parse_address(address)
    return traddr


def _device_to_subsys(dev):
    """
    Resolve the subsystem sysfs path from a device name.
    E.g., "nvme0n1" -> "/sys/class/nvme-subsystem/nvme-subsys0"
    """
    if dev.startswith("nvme"):
        ctrl = dev
        for i in range(len(dev)):
            if not dev[i].isdigit() and dev[i] != 'n':
                ctrl = dev[:i]
                break
        subsys_path = os.path.join(SYS_NVME_SUBSYS,
                                   ctrl.replace("nvme", "nvme-subsys"))
        if os.path.exists(subsys_path):
            return subsys_path
    return None


def _device_to_controller(dev):
    """
    Resolve the controller sysfs path from a device name.
    E.g., "nvme0n1" -> "/sys/class/nvme/nvme0"
    """
    if dev.startswith("nvme"):
        ctrl = dev
        for i in range(len(dev)):
            if not dev[i].isdigit() and dev[i] != 'n':
                ctrl = dev[:i]
                break
        ctrl_path = os.path.join(SYS_NVME, ctrl)
        if os.path.exists(ctrl_path):
            return ctrl_path
    return None


def _parse_address(address):
    """
    Parse NVMe address string like "traddr=192.168.1.100,trsvcid=4420"
    or just "192.168.1.100".
    """
    if not address:
        return (None, None)
    traddr = None
    trsvcid = None
    parts = address.split(",")
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            if key == "traddr":
                traddr = value
            elif key == "trsvcid":
                trsvcid = value
        else:
            if traddr is None:
                traddr = part
    return (traddr, trsvcid or "4420")


def _read_sysfs_attr(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def _raise_connect_error(e):
    stderr = str(e)
    if "authentication" in stderr.lower():
        raise NvmeAuthenticationError(
            "NVMe authentication failed: {}".format(e))
    raise NvmeConnectionError(
        "NVMe connection failed: {}".format(e))


def _parse_list_subsys_output(output):
    """
    Parse the output of `nvme list-subsys` into a dict.
    """
    subsystems = {}
    current_nqn = None
    for line in output.splitlines():
        line = line.strip()
        nqn_match = re.match(r"nvme-subsys(\d+).*NQN=(.+)", line)
        if nqn_match:
            current_nqn = nqn_match.group(2)
            subsystems[current_nqn] = {"paths": []}
            continue
        path_match = re.match(r".*traddr=(.+),trsvcid=(.+)", line)
        if path_match and current_nqn:
            subsystems[current_nqn]["paths"].append({
                "traddr": path_match.group(1),
                "trsvcid": path_match.group(2),
            })
    return subsystems
