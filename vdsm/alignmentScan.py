#
# Copyright (c) 2012, Sasha Tomic <tomic80@gmail.com>
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


from collections import namedtuple
from vdsm.utils import CommandPath
from storage.misc import execCmd

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
    return execCmd(cmd)


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
