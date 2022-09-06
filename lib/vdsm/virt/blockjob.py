# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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

# Require libvirt 6.0, available in RHEL 8.2 and Fedora 30.
# pylint: disable=no-member
if hasattr(libvirt, "VIR_DOMAIN_BLOCK_JOB_TYPE_BACKUP"):
    _TYPE[libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_BACKUP] = "BACKUP"


def type_name(job_type):
    return _TYPE.get(job_type, "Unknown job type {}".format(job_type))
