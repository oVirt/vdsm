#
# Copyright 2012 Red Hat, Inc.
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

GLUSTER_RPM_PACKAGES = (
    ('glusterfs', 'glusterfs'),
    ('glusterfs-fuse', 'glusterfs-fuse'),
    ('glusterfs-geo-replication', 'glusterfs-geo-replication'),
    ('glusterfs-rdma', 'glusterfs-rdma'),
    ('glusterfs-server', 'glusterfs-server'),
    ('gluster-swift', 'gluster-swift'),
    ('gluster-swift-account', 'gluster-swift-account'),
    ('gluster-swift-container', 'gluster-swift-container'),
    ('gluster-swift-doc', 'gluster-swift-doc'),
    ('gluster-swift-object', 'gluster-swift-object'),
    ('gluster-swift-proxy', 'gluster-swift-proxy'),
    ('gluster-swift-plugin', 'gluster-swift-plugin'))

GLUSTER_DEB_PACKAGES = (
    ('glusterfs', 'glusterfs-client'),
    ('glusterfs-fuse', 'libglusterfs0'),
    ('glusterfs-geo-replication', 'libglusterfs0'),
    ('glusterfs-rdma', 'libglusterfs0'),
    ('glusterfs-server', 'glusterfs-server'))
