#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from contextlib import contextmanager
import logging

from vdsm import jobs
from vdsm import properties
from vdsm import qemuimg
from vdsm.storage import constants as sc
from vdsm.storage import guarded
from vdsm.storage import workarounds

from storage import resourceManager as rm
from storage import sd
from storage import volume
from storage.sdc import sdCache

from . import base


class Job(base.Job):
    """
    Copy data from one endpoint to another using qemu-img convert. Currently we
    only support endpoints that are vdsm volumes.
    """
    log = logging.getLogger('storage.sdm.copy_data')

    def __init__(self, job_id, host_id, source, destination):
        super(Job, self).__init__(job_id, 'copy_data', host_id)
        self._source = _create_endpoint(source, host_id, writable=False)
        self._dest = _create_endpoint(destination, host_id, writable=True)
        self._operation = None

    @property
    def progress(self):
        return getattr(self._operation, 'progress', None)

    def _abort(self):
        if self._operation:
            self._operation.abort()

    def _run(self):
        with guarded.context(self._source.locks + self._dest.locks):
            with self._source.prepare(), self._dest.prepare():
                # Do not start copying if we have already been aborted
                if self._status == jobs.STATUS.ABORTED:
                    return

                # Workaround for volumes containing VM configuration info that
                # were created with invalid vdsm metadata.
                if self._source.is_invalid_vm_conf_disk():
                    src_format = dst_format = qemuimg.FORMAT.RAW
                else:
                    src_format = self._source.qemu_format
                    dst_format = self._dest.qemu_format

                self._operation = qemuimg.convert(
                    self._source.path,
                    self._dest.path,
                    srcFormat=src_format,
                    dstFormat=dst_format,
                    backing=self._dest.backing_path,
                    backingFormat=self._dest.backing_qemu_format)
                self._operation.wait_for_completion()


def _create_endpoint(params, host_id, writable):
    endpoint_type = params.pop('endpoint_type')
    if endpoint_type == 'div':
        return CopyDataDivEndpoint(params, host_id, writable)
    else:
        raise ValueError("Invalid or unsupported endpoint %r" % params)


class CopyDataDivEndpoint(properties.Owner):
    sd_id = properties.UUID(required=True)
    img_id = properties.UUID(required=True)
    vol_id = properties.UUID(required=True)

    def __init__(self, params, host_id, writable):
        self.sd_id = params.get('sd_id')
        self.img_id = params.get('img_id')
        self.vol_id = params.get('vol_id')
        self._host_id = host_id
        self._writable = writable
        self._vol = None

    @property
    def locks(self):
        img_ns = sd.getNamespace(sc.IMAGE_NAMESPACE, self.sd_id)
        mode = rm.LockType.exclusive if self._writable else rm.LockType.shared
        ret = [rm.ResourceManagerLock(sc.STORAGE, self.sd_id,
                                      rm.LockType.shared),
               rm.ResourceManagerLock(img_ns, self.img_id, mode)]
        if self._writable:
            ret.append(volume.VolumeLease(self._host_id, self.sd_id,
                                          self.img_id, self.vol_id))
        return ret

    @property
    def path(self):
        return self._vol.getVolumePath()

    def is_invalid_vm_conf_disk(self):
        return workarounds.invalid_vm_conf_disk(self._vol)

    @property
    def qemu_format(self):
        return sc.fmt2str(self._vol.getFormat())

    @property
    def backing_path(self):
        parent_vol = self._vol.getParentVolume()
        if not parent_vol:
            return None
        return volume.getBackingVolumePath(self.img_id, parent_vol.volUUID)

    @property
    def backing_qemu_format(self):
        parent_vol = self._vol.getParentVolume()
        if not parent_vol:
            return None
        return sc.fmt2str(parent_vol.getFormat())

    @contextmanager
    def prepare(self):
        dom = sdCache.produce_manifest(self.sd_id)
        self._vol = dom.produceVolume(self.img_id, self.vol_id)
        self._vol.prepare(rw=self._writable, justme=True)
        try:
            yield
        finally:
            self._vol.teardown(self.sd_id, self.vol_id, justme=True)
