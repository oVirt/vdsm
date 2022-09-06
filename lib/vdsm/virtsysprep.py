# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.virt.utils import LibguestfsCommand

_VIRTSYSPREP = LibguestfsCommand("/usr/bin/virt-sysprep")


def sysprep(vm_id, vol_paths):
    """
    Run virt-sysprep on the list of volumes

    :param vol_paths: list of volume paths
    """
    args = ['--hostname', 'localhost', '--selinux-relabel']
    for vol_path in vol_paths:
        args.extend(('-a', vol_path))

    _VIRTSYSPREP.run(args, log_tag=vm_id)
