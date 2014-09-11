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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import re
import signal

from . import utils

_qemuimg = utils.CommandPath("qemu-img",
                             "/usr/bin/qemu-img",)  # Fedora, EL6


class FORMAT:
    QCOW2 = "qcow2"
    QCOW = "qcow"
    QED = "qed"
    RAW = "raw"
    VMDK = "vmdk"


# Recent qemu-img supports two incompatible qcow2 versions. We use 0.10 format
# so hosts with older qemu can consume images created by newer versions.
# See https://bugzilla.redhat.com/1139707
QCOW2_COMPAT = '0.10'

__iregex = {
    'format': re.compile("^file format: (?P<value>\w+)$"),
    'virtualsize': re.compile("^virtual size: "
                              "[\d.]+[KMGT] \((?P<value>\d+) bytes\)$"),
    'clustersize': re.compile("^cluster_size: (?P<value>\d+)$"),
    'backingfile': re.compile("^backing file: (?P<value>.+) \(actual path"),
    'offset': re.compile("^Image end offset: (?P<value>\d+)$"),
}

INFO_OPTFIELDS_STARTIDX = 4  # qemu-img info optional fields start in this line


class _RegexSearchError(Exception):
    pass


def __iregexSearch(pattern, text):
    m = __iregex[pattern].search(text)
    if m is None:
        raise _RegexSearchError()
    return m.group("value")


class QImgError(Exception):
    def __init__(self, ecode, stdout, stderr, message=None):
        self.ecode = ecode
        self.stdout = stdout
        self.stderr = stderr
        self.message = message

    def __str__(self):
        return "ecode=%s, stdout=%s, stderr=%s, message=%s" % (
            self.ecode, self.stdout, self.stderr, self.message)


def info(image, format=None):
    cmd = [_qemuimg.cmd, "info"]

    if format:
        cmd.extend(("-f", format))

    cmd.append(image)
    rc, out, err = utils.execCmd(cmd, deathSignal=signal.SIGKILL)

    if rc != 0:
        raise QImgError(rc, out, err)

    try:
        info = {
            'format': __iregexSearch("format", out[1]),
            'virtualsize': int(__iregexSearch("virtualsize", out[2])),
        }
    except _RegexSearchError:
        raise QImgError(rc, out, err, "unable to parse qemu-img info output")

    # Scan for optional fields in the output
    row = INFO_OPTFIELDS_STARTIDX
    for field, filterFn in (('clustersize', int), ('backingfile', str)):
        try:
            info[field] = filterFn(__iregexSearch(field, out[row]))
        except (_RegexSearchError, IndexError):
            pass
        else:
            row = row + 1

    return info


def create(image, size=None, format=None, backing=None, backingFormat=None):
    cmd = [_qemuimg.cmd, "create"]
    cwdPath = None

    if format:
        cmd.extend(("-f", format))
        if format == FORMAT.QCOW2 and _supports_qcow2_compat():
            cmd.extend(('-o', 'compat=' + QCOW2_COMPAT))

    if backing:
        if not os.path.isabs(backing):
            cwdPath = os.path.dirname(image)
        cmd.extend(("-b", backing))

    if backingFormat:
        cmd.extend(("-F", backingFormat))

    cmd.append(image)

    if size:
        cmd.append(str(size))

    rc, out, err = utils.execCmd(cmd, cwd=cwdPath, deathSignal=signal.SIGKILL)

    if rc != 0:
        raise QImgError(rc, out, err)


def _supports_qcow2_compat():
    """
    TODO: Remove this when qemu versions providing the "compat" option are
    available on all platforms.
    """
    cmd = [_qemuimg.cmd, "create", "-f", FORMAT.QCOW2, "-o", "?", "/dev/null"]

    rc, out, err = utils.execCmd(cmd, raw=True)

    if rc != 0:
        raise QImgError(rc, out, err)

    # Supported options:
    # compat           Compatibility level (0.10 or 1.1)

    return '\ncompat ' in out


def check(image, format=None):
    cmd = [_qemuimg.cmd, "check"]

    if format:
        cmd.extend(("-f", format))

    cmd.append(image)
    rc, out, err = utils.execCmd(cmd, deathSignal=signal.SIGKILL)

    # FIXME: handle different error codes and raise errors accordingly
    if rc != 0:
        raise QImgError(rc, out, err)
    try:
        check = {
            'offset': int(__iregexSearch("offset", out[1]))
        }
    # TODO: Add requires for qemu supporting offset and print exc_info
    except:
        raise QImgError(rc, out, err, "unable to parse qemu-img check output")

    return check


def convert(srcImage, dstImage, stop, srcFormat=None, dstFormat=None):
    cmd = [_qemuimg.cmd, "convert", "-t", "none"]

    if srcFormat:
        cmd.extend(("-f", srcFormat))

    cmd.append(srcImage)

    if dstFormat:
        cmd.extend(("-O", dstFormat))

    cmd.append(dstImage)

    (rc, out, err) = utils.watchCmd(
        cmd, stop=stop, nice=utils.NICENESS.HIGH, ioclass=utils.IOCLASS.IDLE)

    if rc != 0:
        raise QImgError(rc, out, err)

    return (rc, out, err)


def resize(image, newSize, format=None):
    cmd = [_qemuimg.cmd, "resize"]

    if format:
        cmd.extend(("-f", format))

    cmd.extend((image, str(newSize)))
    rc, out, err = utils.execCmd(cmd, deathSignal=signal.SIGKILL)

    if rc != 0:
        raise QImgError(rc, out, err)


def rebase(image, backing, format=None, backingFormat=None, unsafe=False,
           stop=None):
    cmd = [_qemuimg.cmd, "rebase", "-t", "none"]

    if unsafe:
        cmd.extend(("-u",))

    if format:
        cmd.extend(("-f", format))

    if backingFormat:
        cmd.extend(("-F", backingFormat))

    cmd.extend(("-b", backing, image))

    cwdPath = None if os.path.isabs(backing) else os.path.dirname(image)
    rc, out, err = utils.watchCmd(
        cmd, cwd=cwdPath, stop=stop, nice=utils.NICENESS.HIGH,
        ioclass=utils.IOCLASS.IDLE)

    if rc != 0:
        raise QImgError(rc, out, err)
