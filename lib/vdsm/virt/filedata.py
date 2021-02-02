# Copyright 2020 Red Hat, Inc.
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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Refer to the README and COPYING files for full details of the license.


import base64
import binascii
import grp
import logging
import os
import pwd
import re
import shutil
import time

from vdsm import constants
from vdsm.common import commands
from vdsm.common import exception


# Python shutil implementation is unsafe when extracting malicious
# tar files, let's use tar instead, which is supposed to be safe.
# Also, using tar doesn't require using temporary files.

_TAR = '/usr/bin/tar'


def _make_tar_archive(path):
    return commands.run([_TAR, '-cJC', path, '.'])


def _unpack_tar_archive(path, data):
    os.mkdir(path, 0o700)
    commands.run([_TAR, '-xJC', path], input=data)


class _FileSystemData(object):
    """
    Handling possibly changing data in a local file system.

    VMs sometimes produce and read data stored in a local file system.
    For example, TPM data or secure boot data is stored in certain
    local file system locations and must be stored while a VM is
    running and once it stops running and must be restored before it
    is started again.

    These helper classes facilitate reading and writing the data,
    encoding and decoding it to or from ASCII, and detecting its
    changes.
    """
    def __init__(self, path):
        """
        Define the data to be accessed.

        :param path: absolute path to the data location; its exact
          interpretation is dependent on a particular subclass, e.g. it can be
          a path to the data file or a path to the directory containing the
          data
        :type path: string
        """
        self._path = path

    def _file_timestamp(self, path):
        try:
            return os.stat(path).st_mtime
        except OSError:
            return 0

    def last_modified(self):
        """
        Return the last known modification time of the data.

        The operation is not atomic, for complex data such as
        directory trees modified at once, it may return some
        intermediate timestamp seen when examining the tree.

        :returns: time of the most recent modification seen in
          seconds; if it cannot be obtained, 0 is returned
        :rtype: integer
        """
        return 0

    def _exists(self):
        return os.path.exists(self._path)

    def _retrieve(self):
        raise NotImplementedError

    def retrieve(self, last_modified=-1):
        """
        Retrieve and return data from the file system.

        If the data is not newer than `last_modified`, don't retrieve it.

        :param last_modified: retrieve data only when `last_modified()` returns
          a value newer than this one
        :type last_modified: float
        :returns: encoded data, which can be later used as a `store()`
          argument; None if data is unchanged
        :rtype: string or None
        :raises: `ExternalDataFailed` if the data doesn't exist
        """
        if not self._exists():
            logging.debug("Data path doesn't exist: %s", self._path)
            raise exception.ExternalDataFailed(
                reason="Data path doesn't exist", path=self._path
            )
        currently_modified = self.last_modified()
        if currently_modified <= last_modified and \
           last_modified <= time.time():  # last_modified in future? no!
            return None
        data = self._retrieve()
        return base64.b64encode(data).decode('ascii')

    def _store(self, data):
        raise NotImplementedError

    def store(self, data):
        """
        Store given data to the file system.

        This method is supposed to be called only before a VM is started.
        Contingent stale data, if present, is removed before `data` is stored.
        The method is not intended to be used for an atomic live data
        replacement and such a use is not guaranteed to work properly.

        :param data: encoded data as previously returned from `retrieve()`
        :type data: string
        :raises: exception.ExternalDataFailed if data could not be decoded
        """
        byte_data = data.encode('ascii')
        # Remove line-ends; this is for backward compatibility with legacy
        # base64 methods used in oVirt 4.4.4 and could possibly be dropped the
        # in future
        byte_data = byte_data.translate(None, delete=b'\n')
        error = None
        try:
            decoded_data = base64.b64decode(byte_data, validate=True)
        except binascii.Error as e:
            error = e
        if error is not None:
            raise exception.ExternalDataFailed(
                'Failed to decode base64 data', exception=error)
        self._store(decoded_data)


class FileData(_FileSystemData):
    """
    Handling possibly changing data stored in a local file.

    `path` constructor argument is the file name.
    """

    def last_modified(self):
        return self._file_timestamp(self._path)

    def _retrieve(self):
        with open(self._path, 'rb') as f:
            return f.read()

    def _store(self, data):
        with open(self._path, 'wb') as f:
            f.write(data)


class DirectoryData(_FileSystemData):
    """
    Handling possibly changing data stored in a local directory.

    `path` constructor argument is the directory location.
    """
    def last_modified(self):
        timestamp = 0
        for root, dirs, files in os.walk(self._path):
            timestamp = max(timestamp, self._file_timestamp(root))
            for f in files:
                path = os.path.join(root, f)
                timestamp = max(timestamp, self._file_timestamp(path))
        return timestamp

    def _retrieve(self):
        return _make_tar_archive(self._path)

    def _store(self, data):
        path = self._path
        if os.path.exists(path):
            logging.info("Stale data directory found, removing: %s", path)
            shutil.rmtree(path)
        _unpack_tar_archive(self._path, data)


class Monitor(object):
    """
    Monitoring and reporting file system data.

    This class is useful for watching and retrieving file system data
    that change infrequently.  On each `data()` call, data is
    checked for changes and information about it is updated.  If data
    is unchanged from the last update, it's considered being stable.

    Data is retrieved using `data_retriever` function passed to the
    constructor.  This allows retrieving data from supervdsm using its
    API calls.
    """
    def __init__(self, data_retriever):
        """
        :param data_retriever: function of a single argument,
          last data modification; it returns a pair (DATA, TIMESTAMP)
          where DATA is the data as an encoded string or None if it is
          not newer than the provided last data modification, and
          TIMESTAMP is modification time of the returned data
        :type data_retriever: callable
        """
        self._data_retriever = data_retriever
        self._last_data_hash = None
        self._last_data_change = -1
        self._data_stable = None

    def _data_hash(self, data):
        last_data_hash = self._last_data_hash
        self._last_data_hash = hash(data)
        self._data_stable = last_data_hash == self._last_data_hash
        return self._last_data_hash

    def data_hash(self):
        """
        Return the hash of the last fetched data.

        :returns: hash of the data or None if there is no data
        :rtype: integer or None
        """
        return self._last_data_hash

    def _retrieve_data(self, force):
        last_modified = -1 if force else self._last_data_change
        data, modified = self._data_retriever(last_modified)
        self._last_data_change = modified
        return data

    def data(self, force=False):
        """
        Get the data and return it.

        If data is unchanged from the last call, return None, unless
        `force` is true.

        :param force: iff true then retrieve data and return it even
          when it seems to be unchanged
        :type force: boolean
        :returns: encoded data; None if data is unchanged
        :rtype: string or None
        :raises: OSError -- if an error occurs during data retrieval
        """
        data = self._retrieve_data(force)
        if data is None:
            return None
        self._data_hash(data)
        if self._data_stable and not force:
            return None
        return data


_VM_ID_REGEXP = re.compile(
    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)


def tpm_path(vm_id):
    """
    Return path to TPM data for a VM with the given id.

    :param vm_id: VM id
    :type vm_id: string
    :returns: path to the TPM data directory
    :rtype: string
    :raises: exception.ExternalDataFailed -- if the VM id has invalid format
    """
    # vm_id is used as a subdirectory path by supervdsm, so we must be safe
    # here
    if _VM_ID_REGEXP.match(vm_id) is None:
        raise exception.ExternalDataFailed("Invalid VM id", vm_id=vm_id)
    return os.path.join(constants.P_LIBVIRT_SWTPM, vm_id)


def nvram_path(vm_id):
    """
    Return path to NVRAM file for a VM or a path where to store a template for
    NVRAM of the VM.

    :param vm_id: VM id
    :type vm_id: string
    :returns: path to the NVRAM file
    :rtype: string
    :raises: exception.ExternalDataFailed -- if the VM id has invalid format,
      OSError -- when NVRAM directory cannot be created
    """
    if _VM_ID_REGEXP.match(vm_id) is None:
        raise exception.ExternalDataFailed("Invalid VM id", vm_id=vm_id)
    if not os.path.exists(constants.P_LIBVIRT_NVRAM):
        # The directory is normally created by libvirt, but this may not
        # have happened yet. We can try to create it on our own. The
        # parents however should be part of libvirt RPM and if they
        # are missing it is not our problem.
        uid = pwd.getpwnam(constants.QEMU_PROCESS_USER).pw_uid
        gid = grp.getgrnam(constants.QEMU_PROCESS_GROUP).gr_gid
        os.mkdir(constants.P_LIBVIRT_NVRAM, mode=0o755)
        os.chown(constants.P_LIBVIRT_NVRAM, uid, gid)
    path = os.path.join(constants.P_LIBVIRT_NVRAM, vm_id + ".fd")
    return path
