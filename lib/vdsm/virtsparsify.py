# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.virt.utils import LibguestfsCommand

_VIRTSPARSIFY = LibguestfsCommand("/usr/bin/virt-sparsify")


def sparsify_inplace(vol_path):
    """
    Sparsify the volume in place
    (without copying from an input disk to an output disk)

    :param vol_path: path to the volume
    """
    _VIRTSPARSIFY.run(['--machine-readable', '--in-place', vol_path])
