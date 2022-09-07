# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

# Create replica 3 volume with arbiter.
#
# Requirements:
# - You have 3 nodes (e.g. node1, node2, node3).
# - You create a brick at $brick_name (e.g. /export/vdo0) on every node.
#
# Usage:
#     sh create-gluster-volume.sh my-vol-name /export/my-brick

set -e

vol_name=${1:?vol_name required}
brick_name=${2:?brick_name required}

gluster volume create $vol_name replica 3 arbiter 1 \
    node1:$brick_name \
    node2:$brick_name \
    node3:$brick_name

gluster volume set $vol_name group virt

# Important! without this direct I/O is ignored and gluster domain is created
# as 512 bytes storage.
gluster volume set $vol_name network.remote-dio disable

# Required for vdsm.
gluster volume set $vol_name storage.owner-uid 36
gluster volume set $vol_name storage.owner-gid 36

gluster volume start $vol_name
gluster volume status $vol_name
