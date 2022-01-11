#
# Copyright 2016-2017 Red Hat, Inc.
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
import time

from vdsm import host
from vdsm import jobs
from vdsm import utils

from vdsm.common import properties
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import utils as su
from vdsm.storage import validators
from vdsm.storage import volume
from vdsm.storage import workarounds
from vdsm.storage import sd
from vdsm.storage.sdc import sdCache

from . import base

log = logging.getLogger('storage.sdm.copy_data')


class Job(base.Job):
    """
    Copy data from one endpoint to another using qemu-img convert.
    """

    def __init__(self, job_id, host_id, source, destination,
                 copy_bitmaps=False):
        super(Job, self).__init__(job_id, 'copy_data', host_id)
        self._source = _create_endpoint(
            source, host_id, writable=False, job_id=job_id)
        self._dest = _create_endpoint(
            destination, host_id, writable=True, job_id=job_id,
            is_destination=True)
        self._operation = None
        self._copy_bitmaps = copy_bitmaps

    @property
    def progress(self):
        return getattr(self._operation, 'progress', None)

    def _abort(self):
        if self._operation:
            self._operation.abort()

    def _validate_copy_bitmaps(self, src_format, dst_format):
        if self._copy_bitmaps and qemuimg.FORMAT.RAW in (
                src_format, dst_format):
            raise se.UnsupportedOperation(
                "Cannot copy bitmaps from/to volumes with raw "
                "format",
                src_vol=self._source.path,
                dst_vol=self._dest.path
            )

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

                self._validate_copy_bitmaps(src_format, dst_format)

                with self._dest.volume_operation():
                    self._operation = qemuimg.convert(
                        self._source.path,
                        self._dest.path,
                        srcFormat=src_format,
                        dstFormat=dst_format,
                        dstQcow2Compat=self._dest.qcow2_compat,
                        backing=self._dest.backing_path,
                        backingFormat=self._dest.backing_qemu_format,
                        unordered_writes=self._dest
                            .recommends_unordered_writes,
                        create=self._dest.requires_create,
                        bitmaps=self._copy_bitmaps,
                        target_is_zero=self._dest.zero_initialized,
                    )
                    with utils.stopwatch(
                            "Copy volume {}".format(self._source.path),
                            level=logging.INFO,
                            log=log):
                        self._operation.run()


def _create_endpoint(params, host_id, writable, job_id=None, is_destination=False):
    endpoint_type = params.pop('endpoint_type')
    if endpoint_type == 'div':
        return CopyDataDivEndpoint(params, host_id, writable,
                                   is_destination=is_destination)
    elif endpoint_type == 'external':
        return CopyDataExternalEndpoint(params, host_id, job_id)
    else:
        raise ValueError("Invalid or unsupported endpoint %r" % params)


class CopyDataDivEndpoint(properties.Owner):
    sd_id = properties.UUID(required=True)
    img_id = properties.UUID(required=True)
    vol_id = properties.UUID(required=True)
    generation = properties.Integer(required=False, minval=0,
                                    maxval=sc.MAX_GENERATION)
    prepared = properties.Boolean(default=False)

    def __init__(self, params, host_id, writable, is_destination=False):
        self.sd_id = params.get('sd_id')
        self.img_id = params.get('img_id')
        self.vol_id = params.get('vol_id')
        self.generation = params.get('generation')
        self.prepared = params.get('prepared')
        self.is_destination = is_destination
        self._host_id = host_id
        self._writable = writable
        self._vol = None

    @property
    def locks(self):
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, self.sd_id)
        mode = rm.EXCLUSIVE if self._writable else rm.SHARED
        ret = [rm.Lock(sc.STORAGE, self.sd_id, rm.SHARED),
               rm.Lock(img_ns, self.img_id, mode)]
        if self._writable:
            dom = sdCache.produce_manifest(self.sd_id)
            if dom.hasVolumeLeases():
                ret.append(volume.VolumeLease(self._host_id, self.sd_id,
                                              self.img_id, self.vol_id))
        return ret

    @property
    def path(self):
        return self.volume.getVolumePath()

    def is_invalid_vm_conf_disk(self):
        return workarounds.invalid_vm_conf_disk(self.volume)

    @property
    def qemu_format(self):
        return sc.fmt2str(self.volume.getFormat())

    @property
    def backing_path(self):
        parent_vol = self.volume.getParentVolume()
        if not parent_vol:
            return None
        return volume.getBackingVolumePath(self.img_id, parent_vol.volUUID)

    @property
    def qcow2_compat(self):
        dom = sdCache.produce_manifest(self.sd_id)
        return dom.qcow2_compat()

    @property
    def backing_qemu_format(self):
        parent_vol = self.volume.getParentVolume()
        if not parent_vol:
            return None
        return sc.fmt2str(parent_vol.getFormat())

    @property
    def recommends_unordered_writes(self):
        dom = sdCache.produce_manifest(self.sd_id)
        return dom.recommends_unordered_writes(self.volume.getFormat())

    @property
    def requires_create(self):
        return self.volume.requires_create()

    @property
    def zero_initialized(self):
        return self.volume.zero_initialized()

    @property
    def volume(self):
        if self._vol is None:
            dom = sdCache.produce_manifest(self.sd_id)
            self._vol = dom.produceVolume(self.img_id, self.vol_id)
        return self._vol

    def volume_operation(self):
        return self.volume.operation(self.generation)

    @contextmanager
    def prepare(self):
        if self.prepared:
            yield
        else:
            self.volume.prepare(
                rw=self._writable,
                justme=False,
                allow_illegal=self.is_destination)
            try:
                yield
            finally:
                self.volume.teardown(self.sd_id, self.vol_id, justme=False)


class CopyDataExternalEndpoint(properties.Owner):
    """
    CopyDataExternalEndpoint represents endpoints for volumes not managed by
    vdsm, such as Managed Block Storage volumes.
    """

    url = properties.String(required=True)
    generation = properties.Integer(required=False, minval=0,
                                    maxval=sc.MAX_GENERATION)
    format = properties.String(required=True)
    sparse = properties.Boolean(required=False)
    create = properties.Boolean(required=False)
    is_zero = properties.Boolean(required=True)

    def __init__(self, params, host_id, job_id):
        self.lease = validators.Lease(params.get('lease'))
        self.url = params.get('url')
        self.generation = params.get('generation')
        self.format = params.get('format')
        self.sparse = params.get('sparse', False)
        self.create = params.get('create', True)
        self.is_zero = params.get('is_zero', False)

        self.host_id = host_id
        self.job_id = job_id

    @property
    def locks(self):
        return [
            sd.ExternalLease(
                self.host_id, self.lease.sd_id, self.lease.lease_id),
        ]

    @property
    def path(self):
        return self.url

    def is_invalid_vm_conf_disk(self):
        return False

    @property
    def qemu_format(self):
        return self.format

    @property
    def backing_path(self):
        return None

    @property
    def qcow2_compat(self):
        return "1.1"

    @property
    def backing_qemu_format(self):
        return None

    @property
    def recommends_unordered_writes(self):
        return self.format == "raw" and not self.sparse

    @property
    def requires_create(self):
        return self.create

    @property
    def zero_initialized(self):
        return self.is_zero

    @contextmanager
    def volume_operation(self):
        dom = sdCache.produce_manifest(self.lease.sd_id)
        metadata = dom.get_lvb(self.lease.lease_id)
        log.info(
            "Current lease %s metadata: %r",
            self.lease.sd_id,
            metadata)

        self._validate_metadata(metadata)
        try:
            yield
        except Exception:
            self._update_metadata(dom, metadata, sc.JOB_STATUS_FAILED)
            raise

        self._update_metadata(dom, metadata, sc.JOB_STATUS_SUCCEEDED)

    @contextmanager
    def prepare(self):
        yield

    def _validate_metadata(self, metadata):
        if metadata.get("type") != "JOB":
            raise se.UnsupportedOperation(
                "Metadata type is not support",
                expected="JOB",
                actual=metadata.get("type"))

        if metadata.get("job_id") != self.job_id:
            raise se.UnsupportedOperation(
                "job_id on lease doesn't match passed job_id",
                expected=self.job_id,
                actual=metadata.get("job_id"))

        if metadata.get("job_status") != sc.JOB_STATUS_PENDING:
            raise se.JobStatusMismatch(
                sc.JOB_STATUS_PENDING, metadata.get("job_status"))

        if metadata.get("generation") != self.generation:
            raise se.GenerationMismatch(
                self.generation, metadata.get("generation"))

    def _update_metadata(self, dom, metadata, job_status):
        updated_metadata = metadata.copy()
        updated_metadata["modified"] = int(time.time())
        updated_metadata["host_hardware_id"] = host.uuid()
        updated_metadata["job_status"] = job_status

        if job_status == sc.JOB_STATUS_SUCCEEDED:
            updated_metadata["generation"] = su.next_generation(
                metadata["generation"])

        log.info(
            "Updated lease %s metadata: %r",
            self.lease.sd_id,
            updated_metadata)

        dom.set_lvb(self.lease.lease_id, updated_metadata)
