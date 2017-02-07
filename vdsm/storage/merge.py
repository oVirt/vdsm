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

"""
This is first step of cold merge flow.
This step performs the following:
1. Mark base volume is ILLEGAL in order not allow running the VM while clod
   merge is running.
2. Adjust base volume capacity if top is larger:
   a. For RAW block volume, extend LV
   b. Otherwise, update Vdsm volume metadata
3. Adjust base volume allocation assuming the worst case scenario, i.e.
   extend baes by base-size + top-size.
"""

from __future__ import absolute_import

from contextlib import contextmanager

import logging

from vdsm import constants
from vdsm import properties
from vdsm import qemuimg
from vdsm import utils

from vdsm.config import config
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import resourceManager as rm

from storage import image
from storage import sd
from storage import volume
from storage.sdc import sdCache

log = logging.getLogger('storage.merge')


class SubchainInfo(properties.Owner):
    sd_id = properties.UUID(required=True)
    img_id = properties.UUID(required=True)
    top_id = properties.UUID(required=True)
    base_id = properties.UUID(required=True)
    base_generation = properties.Integer(required=False, minval=0,
                                         maxval=sc.MAX_GENERATION)

    def __init__(self, params, host_id):
        self.sd_id = params.get('sd_id')
        self.img_id = params.get('img_id')
        self.top_id = params.get('top_id')
        self.base_id = params.get('base_id')
        self.base_generation = params.get('base_generation')
        self.host_id = host_id
        self._base_vol = None
        self._top_vol = None
        self._chain = None

    @property
    def base_vol(self):
        if self._base_vol is None:
            dom = sdCache.produce_manifest(self.sd_id)
            self._base_vol = dom.produceVolume(self.img_id,
                                               self.base_id)
        return self._base_vol

    @property
    def top_vol(self):
        if self._top_vol is None:
            dom = sdCache.produce_manifest(self.sd_id)
            self._top_vol = dom.produceVolume(self.img_id,
                                              self.top_id)
        return self._top_vol

    @property
    def chain(self):
        if self._chain is None:
            dom = sdCache.produce_manifest(self.sd_id)
            repoPath = dom.getRepoPath()
            image_repo = image.Image(repoPath)
            chain = image_repo.getChain(self.sd_id, self.img_id)
            # When the VM is cloned from a template, the root volume of the
            # volumes chain is a shared volume. Shared volumes are not returned
            # in the volumes list when calling Image.getChain hence, we have to
            # add that volume manually.
            template = chain[0].getParentVolume()
            if template is not None:
                if not template.isShared():
                    raise se.UnexpectedVolumeState(
                        template.volUUID, "Shared", "Not Shared")
                chain.insert(0, template)
            self._chain = [vol.volUUID for vol in chain]
        return self._chain

    @property
    def locks(self):
        img_ns = sd.getNamespace(sc.IMAGE_NAMESPACE, self.sd_id)
        ret = [rm.ResourceManagerLock(sc.STORAGE, self.sd_id, rm.SHARED),
               rm.ResourceManagerLock(img_ns, self.img_id, rm.EXCLUSIVE)]
        dom = sdCache.produce_manifest(self.sd_id)
        if dom.hasVolumeLeases():
            # We take only the base lease since no other volumes are modified
            ret.append(volume.VolumeLease(self.host_id, self.sd_id,
                                          self.img_id, self.base_id))
        return ret

    def validate(self):
        if self.base_id not in self.chain:
            raise se.VolumeIsNotInChain(self.sd_id,
                                        self.img_id,
                                        self.base_id)

        if self.top_id not in self.chain:
            raise se.VolumeIsNotInChain(self.sd_id,
                                        self.img_id,
                                        self.top_id)

        # Validate that top volume is the parent of the base.
        if self.top_vol.getParent() != self.base_id:
            raise se.WrongParentVolume(self.base_id, self.top_id)

        if self.base_vol.isShared():
            raise se.SharedVolumeNonWritable(self.base_vol)

        if self.top_vol.isShared():
            raise se.SharedVolumeNonWritable(self.top_vol)

    def volume_operation(self):
        return self.base_vol.operation(requested_gen=self.base_generation,
                                       set_illegal=False)

    @contextmanager
    def prepare(self):
        top_index = self.chain.index(self.top_id)
        chain_to_prepare = self.chain[:top_index + 1]
        dom = sdCache.produce_manifest(self.sd_id)
        for vol_id in chain_to_prepare:
            force = True if vol_id == self.base_id else False
            vol = dom.produceVolume(self.img_id, vol_id)
            rw = True if vol_id == self.base_id else False
            # TODO: to improve this late to use subchain.top_vol
            # subchain.base_vol.
            vol.prepare(rw=rw, justme=True, force=force)
        try:
            yield
        finally:
            self.top_vol.teardown(self.sd_id, self.top_id)

    def __repr__(self):
        return ("<SubchainInfo sd_id=%s, img_id=%s, top_id=%s, base_id=%s "
                "base_generation=%s at 0x%x>") % (
            self.sd_id,
            self.img_id,
            self.top_id,
            self.base_id,
            self.base_generation,  # May be None
            id(self),
        )


def prepare(subchain):
    log.info("Preparing subchain %s for merge", subchain)
    with guarded.context(subchain.locks):
        with subchain.prepare():
            subchain.base_vol.setLegality(sc.ILLEGAL_VOL)
            _update_base_capacity(subchain.base_vol,
                                  subchain.top_vol)
            _extend_base_allocation(subchain.base_vol,
                                    subchain.top_vol)


def _update_base_capacity(base_vol, top_vol):
    top_size = top_vol.getSize()
    base_size = base_vol.getSize()
    # TODO: raise if top < base raise some impossible state error.
    if top_size <= base_size:
        return

    if base_vol.getFormat() == sc.RAW_FORMAT:
        log.info("Updating base capacity, extending size of raw base "
                 "volume to %d",
                 top_size)
        # extendSize can run on only SPM so only StorageDomain implement it.
        dom = sdCache.produce(base_vol.sdUUID)
        vol = dom.produceVolume(base_vol.imgUUID, base_vol.volUUID)
        vol.extendSize(top_size)
    else:
        log.info("Updating base capacity, setting size in metadata to "
                 "%d for cow base volume",
                 top_size)
        base_vol.setSize(top_size)


def _extend_base_allocation(base_vol, top_vol):
    if not (base_vol.is_block() and base_vol.getFormat() == sc.COW_FORMAT):
        return

    base_alloc = base_vol.getVolumeSize(bs=1)
    top_alloc = top_vol.getVolumeSize(bs=1)
    vol_chunk_size = (config.getint('irs', 'volume_utilization_chunk_mb') *
                      constants.MEGAB)
    potential_alloc = base_alloc + top_alloc + vol_chunk_size
    # TODO: add chunk_size only if top is leaf.
    capacity = base_vol.getSize() * sc.BLOCK_SIZE
    max_alloc = utils.round(capacity * sc.COW_OVERHEAD, constants.MEGAB)
    actual_alloc = min(potential_alloc, max_alloc)
    actual_alloc_mb = (actual_alloc + constants.MEGAB - 1) / constants.MEGAB
    dom = sdCache.produce_manifest(base_vol.sdUUID)
    dom.extendVolume(base_vol.volUUID, actual_alloc_mb)


def finalize(subchain):
    log.info("Finalizing subchain after merge: %s", subchain)
    with guarded.context(subchain.locks):
        # TODO: As each cold merge step - prepare, merge and finalize -
        # requires different volumes to be prepared, we will add a prepare
        # helper for each step.
        with subchain.prepare():
            subchain.validate()

            # Base volume must be ILLEGAL. Otherwise, VM could be run while
            # performing cold merge.
            base_legality = subchain.base_vol.getLegality()
            if base_legality == sc.LEGAL_VOL:
                raise se.UnexpectedVolumeState(
                    subchain.base_id, sc.ILLEGAL_VOL, base_legality)

            dom = sdCache.produce_manifest(subchain.sd_id)
            _update_qemu_metadata(dom, subchain)
            _update_vdsm_metadata(dom, subchain)
            if subchain.base_vol.chunked():
                # optimal_size must be called when the volume is prepared
                optimal_size = subchain.base_vol.optimal_size()

        if subchain.base_vol.chunked():
            _shrink_base_volume(subchain, optimal_size)

        subchain.base_vol.setLegality(sc.LEGAL_VOL)


def _update_qemu_metadata(dom, subchain):
    children = subchain.top_vol.getChildren()
    if children:
        # Top has children, update qcow metadata by rebasing -u
        child = dom.produceVolume(subchain.img_id,
                                  children[0])
        log.info("Updating qemu metadata, rebasing volume %s into "
                 "volume %s",
                 child.volUUID, subchain.base_vol.volUUID)
        child.prepare(rw=True, justme=True)
        try:
            backing = volume.getBackingVolumePath(subchain.img_id,
                                                  subchain.base_id)
            backing_format = sc.fmt2str(subchain.base_vol.getFormat())
            operation = qemuimg.rebase(image=child.volumePath,
                                       backing=backing,
                                       format=qemuimg.FORMAT.QCOW2,
                                       backingFormat=backing_format,
                                       unsafe=True)
            operation.run()
        finally:
            child.teardown(subchain.sd_id, child.volUUID, justme=True)


def _update_vdsm_metadata(dom, subchain):
    orig_top_id = subchain.chain[-1]
    new_chain = subchain.chain[:]
    new_chain.remove(subchain.top_id)
    log.info("Updating Vdsm metadata, syncing new chain: %s",
             new_chain)
    repoPath = dom.getRepoPath()
    image_repo = image.Image(repoPath)
    image_repo.syncVolumeChain(subchain.sd_id, subchain.img_id, orig_top_id,
                               new_chain)


def _shrink_base_volume(subchain, optimal_size):
    # Must produce a volume because subchain.base_vol is a VolumeManifest,
    # while reduce is implemented on the Volume.
    sd = sdCache.produce(subchain.sd_id)
    base_vol = sd.produceVolume(subchain.img_id, subchain.base_id)
    base_vol.reduce(optimal_size // sc.BLOCK_SIZE)
