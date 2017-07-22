#
# Copyright 2009-2017 Red Hat, Inc.
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
from vdsm.storage import qemuimg

CLUSTER_SIZE = 64 * 1024
SIZEOF_INT_64 = 8

# Width of refcount block entry configured in the QCOW header
REFCOUNT_ORDER = 4


def _align_offset(offset, n):
    offset = (offset + n - 1) & ~(n - 1)
    return offset


def _ctz32(val):
    # Binary search for the trailing one bit.
    cnt = 0
    if not (val & 0x0000FFFF):
        cnt += 16
        val >>= 16
    if not (val & 0x000000FF):
        cnt += 8
        val >>= 8
    if not (val & 0x0000000F):
        cnt += 4
        val >>= 4
    if not (val & 0x00000003):
        cnt += 2
        val >>= 2
    if not (val & 0x00000001):
        cnt += 1
        val >>= 1
    if not (val & 0x00000001):
        cnt += 1
    return cnt


def _div_round_up(n, d):
    return (n + d - 1) // d


def _estimate_metadata_size(virtual_size):
    """
    This code is ported from the qemu calculation implemented in block/qcow2.c
    in the method qcow2_create2
    """
    # cluster_bits = ctz32(cluster_size);
    cluster_bits = _ctz32(CLUSTER_SIZE)

    # int64_t aligned_total_size = align_offset(total_size, cluster_size);
    aligned_total_size = _align_offset(virtual_size, CLUSTER_SIZE)

    # /* see qcow2_open() */
    # refblock_bits = cluster_bits - (refcount_order - 3);
    # refblock_size = 1 << refblock_bits;
    refblock_bits = cluster_bits - (REFCOUNT_ORDER - 3)
    refblock_size = 1 << refblock_bits

    # Header: 1 cluster
    # meta_size += cluster_size;
    meta_size = CLUSTER_SIZE

    # Total size of L2 tables:
    #   nl2e = aligned_total_size / cluster_size;
    #   nl2e = align_offset(nl2e, cluster_size / sizeof(uint64_t));
    #   meta_size += nl2e * sizeof(uint64_t);
    nl2e = aligned_total_size // CLUSTER_SIZE
    nl2e = _align_offset(int(nl2e), CLUSTER_SIZE // SIZEOF_INT_64)
    meta_size += nl2e * SIZEOF_INT_64

    # Total size of L1 tables:
    #   nl1e = nl2e * sizeof(uint64_t) / cluster_size;
    #   nl1e = align_offset(nl1e, cluster_size / sizeof(uint64_t));
    #   meta_size += nl1e * sizeof(uint64_t);
    nl1e = nl2e * SIZEOF_INT_64 / CLUSTER_SIZE
    nl1e = _align_offset(int(nl1e), CLUSTER_SIZE // SIZEOF_INT_64)
    meta_size += nl1e * SIZEOF_INT_64

    #  total size of refcount blocks
    #  note: every host cluster is reference-counted,
    #  including metadata
    #  (even refcount blocks are recursively included).
    #  Let:
    #    a = total_size (this is the guest disk size)
    #    m = meta size not including refcount blocks
    #        and refcount tables
    #    c = cluster size
    #    y1 = number of refcount blocks entries
    #    y2 = meta size including everything
    #    rces = refcount entry size in byte then,
    #          y1 = (y2 + a)/c
    #          y2 = y1 * rces + y1 * rces * sizeof(u64) / c + m
    #          we can get y1:
    #          y1 = (a + m) / (c - rces - rces * sizeof(u64) / c)
    # /* refcount entry size in bytes */
    # double rces = (1 << refcount_order) / 8.;
    rces = (1 << REFCOUNT_ORDER) / 8.

    # nrefblocke = (aligned_total_size + meta_size + cluster_size)
    #               / (cluster_size - rces - rces * sizeof(uint64_t)
    #               / cluster_size);
    nrefblocke = ((aligned_total_size + meta_size + CLUSTER_SIZE) /
                  (CLUSTER_SIZE - rces - rces * SIZEOF_INT_64 / CLUSTER_SIZE))

    # meta_size += DIV_ROUND_UP(nrefblocke, refblock_size) * cluster_size;
    meta_size += _div_round_up(nrefblocke, refblock_size) * CLUSTER_SIZE

    # total size of refcount tables:
    # nreftablee = nrefblocke / refblock_size;
    nreftablee = nrefblocke / refblock_size

    # We should always have at least 1 cluster for refcount table.
    if (nreftablee < 1):
        nreftablee = 1

    # nreftablee = align_offset(nreftablee, cluster_size / sizeof(uint64_t));
    nreftablee = _align_offset(int(nreftablee), CLUSTER_SIZE // SIZEOF_INT_64)

    # meta_size += nreftablee * sizeof(uint64_t);
    meta_size += nreftablee * SIZEOF_INT_64

    # Return the meta data size of the converted image.
    return int(meta_size)


def estimate_size(filename):
    """
    Estimating qcow2 file size once converted from raw to qcow2.
    The filename is a path (sparse or preallocated),
    or a path to preallocated block device.
    """
    info = qemuimg.info(filename)
    if (info['format'] != qemuimg.FORMAT.RAW):
        raise ValueError("Estimate size is only supported for raw format. file"
                         " %s is with format %s" % (filename, info['format']))

    # Get used clusters and virtual size of destination volume.
    virtual_size = info['virtualsize']
    meta_size = _estimate_metadata_size(virtual_size)
    runs = qemuimg.map(filename)
    used_clusters = count_clusters(runs)

    # Return the estimated size.
    return meta_size + used_clusters * CLUSTER_SIZE


def count_clusters(runs):
    count = 0
    last = -1
    for r in runs:
        # Find the cluster when start and end are located.
        start = r["start"] // CLUSTER_SIZE
        end = (r["start"] + r["length"]) // CLUSTER_SIZE
        if r["data"]:
            if start == end:
                # This run is smaller than a cluster. If we have several runs
                # in the same cluster, we want to count the cluster only once.
                if start != last:
                    count += 1
            else:
                # This run span over multiple clusters - we want to count all
                # the clusters this run touches.
                count += end - start
            last = end
    return count
