# SPDX-FileCopyrightText: Sasha Tomic <tomic80@gmail.com>
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os
from collections import namedtuple
from vdsm.common.cmdutils import CommandPath
from vdsm.storage.misc import execCmd

ScanOutput = namedtuple(
    'ScanOutput',
    ['partitionName', 'partitionStartBytes', 'partitionAlignment',
     'alignmentScanResult', 'alignmentScanExplanation'])

_virtAlignmentScan = CommandPath("virt-alignment-scan",
                                 "/usr/bin/virt-alignment-scan",  # Fedora, EL6
                                 )


class VirtAlignError(Exception):
    pass


def runScanArgs(*args):
    cmd = [_virtAlignmentScan.cmd]
    cmd.extend(args)
    # TODO: remove the environment when the issue in
    # virt-alignment-scan/libvirt is resolved
    # http://bugzilla.redhat.com/1151838
    env = os.environ.copy()
    env['LIBGUESTFS_BACKEND'] = 'direct'
    return execCmd(cmd, env=env)


def scanImage(image_path):
    image_path = image_path.strip()
    rc, out, err = runScanArgs('--add', image_path)
    if rc == 0:
        pass
    elif rc == 1:
        raise VirtAlignError("An error scanning the disk image "
                             "or guest:\n%s" % err)
    elif rc == 2:
        # Successful exit, some partitions have alignment < 64K
        # which can result in poor performance on high end network storage.
        pass
    elif rc == 3:
        # Successful exit, some partitions have alignment < 4K
        # which can result in poor performance on most hypervisors.
        pass
    else:
        raise ValueError("Unknown return code from "
                         "virt-alignment-scan: %d" % rc)
    outList = []
    for line in out:
        line = line.split(None, 3)
        partName = line[0]  # the device and partition name (eg. "/dev/sda1")
        partStart = int(line[1])  # the start of the partition in bytes
        partAlignment = line[2]  # in bytes or Kbytes (eg. 512 or "4K")
        scanResult = (line[3] == "ok")  # True if aligned, otherwise False
        scanExplanation = line[3]  # optional free-text explanation
        outList.append(ScanOutput(partName, partStart, partAlignment,
                                  scanResult, scanExplanation))
    return outList
