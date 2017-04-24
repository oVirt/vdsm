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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import os
import re
import stat
import uuid

from vdsm.constants import P_LIBVIRT_VMCHANNELS, P_OVIRT_VMCONSOLES
from vdsm.storage.fileUtils import resolveGid
from vdsm import numa

from . import expose


@expose
def prepareVmChannel(socketFile, group=None):
    if (socketFile.startswith(P_LIBVIRT_VMCHANNELS) or
       socketFile.startswith(P_OVIRT_VMCONSOLES)):
        fsinfo = os.stat(socketFile)
        mode = fsinfo.st_mode | stat.S_IWGRP
        os.chmod(socketFile, mode)
        if group is not None:
            os.chown(socketFile,
                     fsinfo.st_uid,
                     resolveGid(group))
    else:
        raise Exception("Incorporate socketFile")


@expose
def getVmPid(vmName):
    pidFile = "/var/run/libvirt/qemu/%s.pid" % vmName
    with open(pidFile) as pid:
        return pid.read()


@expose
def getVcpuNumaMemoryMapping(vmName):
    vmPid = getVmPid(vmName)
    vCpuPids = numa.getVcpuPid(vmName)
    vCpuIdxToNode = {}
    for vCpuIndex, vCpuPid in vCpuPids.iteritems():
        numaMapsFile = "/proc/%s/task/%s/numa_maps" % (vmPid, vCpuPid)
        try:
            with open(numaMapsFile, 'r') as f:
                mappingNodes = map(
                    int, re.findall('N(\d+)=\d+', f.read()))
                vCpuIdxToNode[vCpuIndex] = list(set(mappingNodes))
        except IOError:
            continue
    return vCpuIdxToNode


@expose
def mdev_create(device, mdev_type, mdev_uuid=None):
    """Create the desired mdev type.

    Args:
        device: PCI address of the parent device in the format
            (domain:bus:slot.function). Example:  0000:06:00.0.
        mdev_type: Type to be spawned. Example: nvidia-11.
        mdev_uuid: UUID for the spawned device. Keeping None generates a new
            UUID.

    Returns:
        UUID (string) of the created device.

    Raises:
        Possibly anything related to sysfs write (IOError).
    """
    path = os.path.join(
        '/sys/class/mdev_bus/{}/mdev_supported_types/{}/create'.format(
            device, mdev_type
        )
    )

    if mdev_uuid is None:
        mdev_uuid = str(uuid.uuid4())

    with open(path, 'w') as f:
        f.write(mdev_uuid)

    return mdev_uuid
