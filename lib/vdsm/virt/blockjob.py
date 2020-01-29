#
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
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import libvirt

# Map virDomainBlockJobType to name.
_TYPE = {
    libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_UNKNOWN: "UNKNOWN",
    libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_PULL: "PULL",
    libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COPY: "COPY",
    libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT: "COMMIT",
    libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_ACTIVE_COMMIT: "ACTIVE_COMMIT",
}


def type_name(job_type):
    return _TYPE.get(job_type, "Unknown job type {}".format(job_type))
