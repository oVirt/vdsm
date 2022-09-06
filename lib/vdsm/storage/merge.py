# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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

from vdsm.common import properties
from vdsm.common.units import MiB

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import image
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import volume
from vdsm.storage.sdc import sdCache

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
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, self.sd_id)
        ret = [rm.Lock(sc.STORAGE, self.sd_id, rm.SHARED),
               rm.Lock(img_ns, self.img_id, rm.EXCLUSIVE)]
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
            vol = dom.produceVolume(self.img_id, vol_id)
            rw = True if vol_id == self.base_id else False
            # TODO: to improve this late to use subchain.top_vol
            # subchain.base_vol.
            vol.prepare(rw=rw, justme=True)
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
            _update_base_capacity(subchain.base_vol,
                                  subchain.top_vol)
            _extend_base_allocation(subchain.base_vol,
                                    subchain.top_vol)


def _update_base_capacity(base_vol, top_vol):
    top_capacity = top_vol.getCapacity()
    base_capacity = base_vol.getCapacity()
    # TODO: raise if top < base raise some impossible state error.
    if top_capacity <= base_capacity:
        return

    if base_vol.getFormat() == sc.RAW_FORMAT:
        log.info("Updating base capacity, extending size of raw base "
                 "volume to %d", top_capacity)
        # extendSize can run on only SPM so only StorageDomain implement it.
        dom = sdCache.produce(base_vol.sdUUID)
        vol = dom.produceVolume(base_vol.imgUUID, base_vol.volUUID)
        vol.extendSize(top_capacity)
    else:
        log.info("Updating base capacity, setting size in metadata to "
                 "%d for cow base volume", top_capacity)
        base_vol.setCapacity(top_capacity)


def _extend_base_allocation(base_vol, top_vol):
    if not (base_vol.is_block() and base_vol.getFormat() == sc.COW_FORMAT):
        return

    # Measure the subchain from top to base. This gives us the required
    # allocation for merging top into base.
    log.debug("Measuring sub chain top=%r base=%r",
              top_vol.volUUID, base_vol.volUUID)
    measure = qemuimg.measure(
        top_vol.getVolumePath(),
        format=qemuimg.FORMAT.QCOW2,
        output_format=qemuimg.FORMAT.QCOW2,
        is_block=True,
        base=base_vol.getVolumePath())
    log.debug("Measure result: %s", measure)

    # When merging we always copy the bitmaps from the top to base. Measure
    # gives us the size of the bitmaps in top *and* base, so this may allocate
    # more than needed, but bitmaps are small so it should be good enough.
    required_size = measure["required"] + measure.get("bitmaps", 0)

    # If the top volume is leaf, the base volume will become leaf after the
    # merge, so it needs more space.
    optimal_size = base_vol.optimal_cow_size(
        required_size, base_vol.getCapacity(), top_vol.isLeaf())

    # Extend the volume.
    dom = sdCache.produce(base_vol.sdUUID)
    dom.extendVolume(base_vol.volUUID, optimal_size // MiB)


def finalize(subchain):
    """
    During finalize we distunguish between leaf merge and internal merge.

    In case of leaf merge, we only upate vdsm metadata, i.e. we call
    syncVolumeChain that marks the top volume as ILLEGAL. If the operation
    succeeds, the top volume is marked as ILLEGAL and will be removed by the
    engine. In case of failure, if the top volume is LEGAL, the user can
    recover by retrying cold merge. If the top volume is ILLEGAL, and the
    engine fails to delete the volume, a manual recovery is required.

    In case of internal merge, we need to update qcow metadata and vdsm
    metadata. For qcow metadata, we rebase top's child on base, and for vdsm
    metadata, we invoke syncVolumeChain that changes the child of the top to
    point to the base as its parent.  As we would like to minimize the window
    where the top volume is ILLEGAL, we set it to ILLEGAL just before calling
    qemuimg rebase.

    After finalize internal merge, there are three possible states:
    1. top volume illegal, qemu and vdsm chains updated. The operation will be
       finished by the engine deleting the top volume.
    2. top volume is ILLEGAL but not rebased, both qemu chain and vdsm chain
       are synchronized. Manual recovery is possible by inspecting the chains
       and setting the top volume to legal.
    3. top volume is ILLEGAL, qemu chain rebased, but vdsm chain wasn't
       modified or partly modified. Manual recovery is possible by updating
       vdsm chain.
    """
    log.info("Finalizing subchain after merge: %s", subchain)
    with guarded.context(subchain.locks):
        # TODO: As each cold merge step - prepare, merge and finalize -
        # requires different volumes to be prepared, we will add a prepare
        # helper for each step.
        with subchain.prepare():
            subchain.validate()
            dom = sdCache.produce_manifest(subchain.sd_id)
            if subchain.top_vol.isLeaf():
                _finalize_leaf_merge(dom, subchain)
            else:
                _finalize_internal_merge(dom, subchain)

            if subchain.base_vol.can_reduce():
                # If the top volume is leaf, the base volume will become a leaf
                # after the top volume is deleted.
                optimal_size = subchain.base_vol.optimal_size(
                    as_leaf=subchain.top_vol.isLeaf())
                actual_size = subchain.base_vol.getVolumeSize()

        # Optimal size must be computed while the image is prepared, but
        # reducing with the volume still active will issue a warning from LVM.
        # Thus, reduce after having teardown the volume.
        if subchain.base_vol.can_reduce() and optimal_size < actual_size:
            _shrink_base_volume(subchain, optimal_size)


def _finalize_leaf_merge(dom, subchain):
    _update_vdsm_metadata(dom, subchain)


def _finalize_internal_merge(dom, subchain):
    children = subchain.top_vol.getChildren()
    child = dom.produceVolume(subchain.img_id, children[0])
    rebase = _rebase_operation(subchain.base_vol, child)
    child.prepare(rw=True, justme=True)
    try:
        subchain.top_vol.setLegality(sc.ILLEGAL_VOL)
        try:
            rebase.run()
        except:
            # Set top volume to legal to enable recovery by retrying the merge.
            _rollback_top_volume_legality(subchain.top_vol)
            raise
        _update_vdsm_metadata(dom, subchain)
    finally:
        child.teardown(subchain.sd_id, child.volUUID, justme=True)


def _rollback_top_volume_legality(top_vol):
    # Wrapping the next call in a try-except block is neeed in order to raise
    # the original exception raised in _finalize_internal_merge.
    try:
        top_vol.setLegality(sc.LEGAL_VOL)
    except Exception:
        log.exception("Failed to set top volume %s as legal", top_vol.volUUID)


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


def _rebase_operation(base, child):
    backing = volume.getBackingVolumePath(base.imgUUID, base.volUUID)
    backing_format = sc.fmt2str(base.getFormat())
    operation = qemuimg.rebase(image=child.volumePath,
                               backing=backing,
                               format=qemuimg.FORMAT.QCOW2,
                               backingFormat=backing_format,
                               unsafe=True)
    return operation


def _shrink_base_volume(subchain, optimal_size):
    # Must produce a volume because subchain.base_vol is a VolumeManifest,
    # while reduce is implemented on the Volume.
    sd = sdCache.produce(subchain.sd_id)
    base_vol = sd.produceVolume(subchain.img_id, subchain.base_id)
    base_vol.reduce(optimal_size)
