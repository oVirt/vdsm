# Copyright 2016-2021 Red Hat, Inc.
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
import os
import stat
import tempfile
import uuid

from vdsm.constants import P_LIBVIRT_VMCHANNELS, P_OVIRT_VMCONSOLES
from vdsm.storage.fileUtils import resolveGid
from vdsm.virt import filedata
from vdsm.common import exception
from vdsm.common import password
from vdsm.common.fileutils import parse_key_val_file

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
def hugepages_alloc(count, path):
    """
    Function to allocate hugepages. Thread-safety not guaranteed.
    The default size depends on the architecture:
        x86_64: 2 MiB
        POWER8: 16 MiB

    Args:
        count (int): Number of huge pages to be allocated. Negative count
        deallocates pages.

    Returns:
        int: The number of successfully allocated hugepages.
    """
    existing_pages = 0
    allocated_pages = 0

    with open(path, 'r') as f:
        existing_pages = int(f.read())

    count = max(-existing_pages, count)

    with open(path, 'w') as f:
        f.write(str(existing_pages + count))

    with open(path, 'r') as f:
        allocated_pages = int(f.read()) - existing_pages

    return allocated_pages


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


@expose
def mdev_delete(device, mdev_uuid):
    """

    Args:
        device: PCI address of the parent device in the format
            (domain:bus:slot.function). Example:  0000:06:00.0.
        mdev_type: Type to be spawned. Example: nvidia-11.
        mdev_uuid: UUID for the spawned device. Keeping None generates a new
            UUID.

    Raises:
        Possibly anything related to sysfs write (IOError).
    """
    path = os.path.join(
        '/sys/class/mdev_bus/{}/{}/remove'.format(
            device, mdev_uuid
        )
    )

    with open(path, 'w') as f:
        f.write('1')


QEMU_CONFIG_FILE = '/etc/libvirt/qemu.conf'


@expose
def check_qemu_conf_contains(key, value):
    """
    Checks if qemu.conf contains the given key-value config.
    """
    try:
        kvs = parse_key_val_file(QEMU_CONFIG_FILE)
        return kvs.get(key, '0') == value
    except:
        logging.error('Could not check %s for %s', QEMU_CONFIG_FILE, key)
        # re-raised exception will be logged, no need to log it here
        raise


@expose
def read_tpm_data(vm_id, last_modified):
    """
    Return TPM data of the given VM.

    If data is not newer than `last_modified`, return None.
    In addition to data, the last detected data modification time is
    returned; the returned data may be newer, but never older than the
    returned time.

    :param vm_id: VM id
    :type vm_id: string
    :param last_modified: if data file system time stamp is not
      newer than this time in seconds, None is returned
    :type last_modified: float
    :returns: tuple (DATA, MODIFIED) where DATA is encoded TPM data suitable to
      use in `write_tpm_data()`, wrapped by `password.ProtectedPassword`,
      or None, and MODIFIED is DATA modification time (which may be older than
      actual modification time)
    :rtype: tuple
    """
    accessor = filedata.DirectoryData(filedata.tpm_path(vm_id),
                                      compress=False)
    currently_modified = accessor.last_modified()
    data = accessor.retrieve(last_modified=last_modified)
    return password.ProtectedPassword(data), currently_modified


@expose
def write_tpm_data(vm_id, tpm_data):
    """
    Write TPM data for the given VM.

    :param vm_id: VM id
    :type vm_id: string
    :param tpm_data: encoded TPM data as previously obtained from
      `read_tpm_data()`
    :type tpm_data: ProtectedPassword
    """
    tpm_data = password.unprotect(tpm_data)
    # Permit only archives with plain files and directories to prevent various
    # kinds of attacks.
    with tempfile.TemporaryDirectory() as d:
        accessor = filedata.DirectoryData(os.path.join(d, 'check'))
        accessor.store(tpm_data)
        for root, dirs, files in os.walk(d):
            for f in files:
                path = os.path.join(root, f)
                if not os.path.isfile(path):
                    logging.error("Special file in TPM data: %s", path)
                    raise exception.ExternalDataFailed(
                        reason="Cannot write TPM data with non-regular files",
                        path=path
                    )
    # OK, write the data to the target location
    accessor = filedata.DirectoryData(filedata.tpm_path(vm_id))
    accessor.store(tpm_data)


@expose
def read_nvram_data(vm_id, last_modified):
    accessor = filedata.FileData(filedata.nvram_path(vm_id))
    currently_modified = accessor.last_modified()
    data = accessor.retrieve(last_modified=last_modified)
    return password.ProtectedPassword(data), currently_modified


@expose
def write_nvram_data(vm_id, nvram_data):
    nvram_data = password.unprotect(nvram_data)
    nvram_path = filedata.nvram_path(vm_id)
    # Create the file with restricted permissions owned by root
    if os.path.exists(nvram_path):
        os.remove(nvram_path)
    fd = os.open(
        nvram_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=0o600)
    os.close(fd)
    # Write content
    accessor = filedata.FileData(nvram_path)
    accessor.store(nvram_data)
