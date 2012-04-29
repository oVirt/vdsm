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
from storage.misc import execCmd

ScanOutput = namedtuple('ScanOutput',
  ['partition_name', 'partition_start_bytes', 'partition_alignment',
  'alignment_scan_result', 'alignment_scan_explanation'])


class VirtAlignError(Exception):
    pass


def runScanArgs(*args):
    cmd = ['/usr/bin/virt-alignment-scan']
    cmd.extend(args)
    return execCmd(cmd)


def scanImage(image_path):
    image_path = image_path.strip()
    retcode, out, err = runScanArgs('--add', image_path)
    if retcode == 0:
        pass
    elif retcode == 1:
        raise VirtAlignError("An error scanning the disk image " \
                             "or guest:\n%s" % err)
    elif retcode == 2:
        # Successful exit, some partitions have alignment < 64K
        # which can result in poor performance on high end network storage.
        pass
    elif retcode == 3:
        # Successful exit, some partitions have alignment < 4K
        # which can result in poor performance on most hypervisors.
        pass
    else:
        raise VirtAlignError("Unexpected return code from " \
                             "virt-alignment-scan: %d" % retcode)
    out_list = []
    for line in out:
        line = line.split(None, 3)
        part_name = line[0]  # the device and partition name (eg. "/dev/sda1")
        part_start = int(line[1])  # the start of the partition in bytes
        part_alignment = line[2]  # in bytes or Kbytes (eg. 512 or "4K")
        scan_result = (line[3] == "ok")  # True if aligned, otherwise False
        scan_explanation = line[3]  # optional free-text explanation
        out_list.append(ScanOutput(part_name, part_start, part_alignment,
                                    scan_result, scan_explanation))
    return out_list
