# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import subprocess

VIRT_HOST_VALIDATE_CMD = "virt-host-validate"
CMD="date"



def exec_validate_cmd(cmd):
    """Execute a command and convert returned values to native string.

    Note that this function should not be used if output data could be
    undecodable bytes.
    """
    try:
        out = subprocess.check_output(cmd).decode("utf-8")
    except Exception as err:
        logging.exception(f"Unexpected {err}, {type(err)}")

    logging.info(f"output {out}")
    return out




def is_valid_virt_host():
    """ Validate host is valid for virtualization
    Below is the command and output
    If host is not valid, the failure will be logged
    # virt-host-validate
    QEMU: Checking if device /dev/kvm exists                                   : PASS
    QEMU: Checking if device /dev/kvm is accessible                            : PASS
    QEMU: Checking if device /dev/vhost-net exists                             : PASS
    QEMU: Checking if device /dev/net/tun exists                               : PASS
    QEMU: Checking for cgroup 'cpu' controller support                         : PASS
    QEMU: Checking for cgroup 'cpuacct' controller support                     : PASS
    QEMU: Checking for cgroup 'cpuset' controller support                      : PASS
    QEMU: Checking for cgroup 'memory' controller support                      : PASS
    QEMU: Checking for cgroup 'devices' controller support                     : PASS
    QEMU: Checking for cgroup 'blkio' controller support                       : PASS
    QEMU: Checking for device assignment IOMMU support                         : WARN (Unknown if this platform has IOMMU support)
    QEMU: Checking for secure guest support                                    : WARN (Unknown if this platform has Secure Guest support)

    """

    out = exec_validate_cmd(VIRT_HOST_VALIDATE_CMD)

    if (not 'fail' in out.lower()) :
        valid_host = True


    return valid_host
