#
# Copyright 2012-2017 Red Hat, Inc.
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
import json
import logging
import os
import re

from vdsm.common import cache
from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import exception
from vdsm.common.units import GiB
from vdsm.config import config
from vdsm.storage import operation

_qemuimg = cmdutils.CommandPath(
    "qemu-img", "/usr/local/bin/qemu-img", "/usr/bin/qemu-img")

_log = logging.getLogger("storage.qemuimg")


class FORMAT:
    QCOW2 = "qcow2"
    QCOW = "qcow"
    QED = "qed"
    RAW = "raw"
    VMDK = "vmdk"

_QCOW2_COMPAT_SUPPORTED = ("0.10", "1.1")


class PREALLOCATION:
    """
    Possible preallocation modes for qemu
    """

    # No preallocation at all.
    OFF = "off"

    # Allocates just image metadata. Could be used only with qcow2 format.
    METADATA = "metadata"

    # Preallocates space by calling posix_fallocate().
    FALLOC = "falloc"

    # Preallocates space for image by writing zeros to underlying storage.
    FULL = "full"


def supports_compat(compat):
    return compat in _QCOW2_COMPAT_SUPPORTED


class InvalidOutput(cmdutils.Error):
    msg = ("Commmand {self.cmd} returned invalid output: {self.out}: "
           "{self.reason}")

    def __init__(self, cmd, out, reason):
        self.cmd = cmd
        self.out = out
        self.reason = reason


def info(image, format=None, unsafe=False, trusted_image=True,
         backing_chain=False):
    cmd = [_qemuimg.cmd, "info", "--output", "json"]

    if format:
        cmd.extend(("-f", format))

    if unsafe:
        cmd.append('-U')

    if backing_chain:
        cmd.append('--backing-chain')

    cmd.append(image)

    if not trusted_image:
        # NOQA (long urls)
        #
        # Turns out qemu image parser is not hardened against malicious input
        # and can be abused to allocate an arbitrary amount of memory and/or
        # dump a lot of information when used with "--output=json".
        #
        # These values were recommended by Daniel P. Berrange in:
        # http://lists.nongnu.org/archive/html/qemu-block/2018-07/msg00488.html
        #
        # Richard W.M. Jones adds more info here:
        # http://lists.nongnu.org/archive/html/qemu-block/2018-07/msg00547.html
        #
        # The 30 seconds cpu_time value came from this bug:
        # https://bugs.launchpad.net/nova/+bug/1705340
        # Showing qemu-img info take more then 8 seconds with 120G snapshot.
        #
        # TODO: It does not make sense that qemu-img will need 30 seconds of
        # cpu time for reading valid image qcow2 header, or checking the size
        # of a raw image. Investigate why we need these values.
        cmd = cmdutils.prlimit(cmd, cpu_time=30, address_space=GiB)

    out = _run_cmd(cmd)

    try:
        info = _parse_qemuimg_json(out, list if backing_chain else dict)
    except ValueError as e:
        raise InvalidOutput(
            cmd, out, "Failed to process qemu-img output: %s" % e)

    chain = info if backing_chain else [info]
    for node in chain:
        for key in ("virtual-size", "format"):
            if key not in node:
                raise InvalidOutput(cmd, out, "Missing field: %r" % key)

    return info


def measure(image, format=None, output_format=None, backing=True,
            is_block=False):
    cmd = [_qemuimg.cmd, "measure", "--output", "json"]

    if not format and not backing:
        raise ValueError("backing=False requires specifying image format")

    if output_format:
        cmd.extend(("-O", output_format))

    protocol = "host_device" if is_block else "file"
    node = {"file": {"driver": protocol, "filename": image}}
    if format:
        node["driver"] = format
        if format == FORMAT.QCOW2 and not backing:
            node["backing"] = None

    cmd.append("json:" + json.dumps(node))

    out = _run_cmd(cmd)
    try:
        qemu_measure = _parse_qemuimg_json(out)
    except ValueError:
        raise InvalidOutput(cmd, out, "Failed to process qemu-img output")

    for key in ("required", "fully-allocated"):
        if key not in qemu_measure:
            raise InvalidOutput(cmd, out, "Missing field: %r" % key)

    return qemu_measure


def create(image, size=None, format=None, qcow2Compat=None,
           backing=None, backingFormat=None, preallocation=None, unsafe=False):
    cmd = [_qemuimg.cmd, "create"]
    cwdPath = None

    if format:
        cmd.extend(("-f", format))
        if format == FORMAT.QCOW2:
            qcow2Compat = _validate_qcow2_compat(qcow2Compat)
            cmd.extend(('-o', 'compat=' + qcow2Compat))

    if backing:
        if not os.path.isabs(backing):
            cwdPath = os.path.dirname(image)
        cmd.extend(("-b", backing))

    if backingFormat:
        cmd.extend(("-F", backingFormat))

    if preallocation:
        cmd.extend(("-o", "preallocation=" +
                    _get_preallocation(preallocation, format)))

    if unsafe:
        cmd.append('-u')

    cmd.append(image)

    if size is not None:
        cmd.append(str(size))

    return operation.Command(cmd, cwd=cwdPath)


def check(image, format=None):
    cmd = [_qemuimg.cmd, "check", "--output", "json"]

    if format:
        cmd.extend(("-f", format))

    cmd.append(image)
    try:
        out = _run_cmd(cmd)
    except cmdutils.Error as e:
        # Return code 3 means that leaked clusters were found on the image.
        # This means waste of disk space, but no harm to data. Despite this
        # return code, we still get the check info in the stdout.
        if e.rc != 3:
            raise
        out = e.out
    try:
        qemu_check = _parse_qemuimg_json(out)
    except ValueError:
        raise InvalidOutput(cmd, out, "Failed to process qemu-img output")
    if "leaks" in qemu_check:
        _log.warning("%d leaked clusters found on the image",
                     qemu_check["leaks"])
    try:
        return {"offset": qemu_check["image-end-offset"]}
    except KeyError:
        raise InvalidOutput(cmd, out, "unable to parse qemu-img check output")


def convert(srcImage, dstImage, srcFormat=None, dstFormat=None,
            dstQcow2Compat=None, backing=None, backingFormat=None,
            preallocation=None, compressed=False, unordered_writes=False,
            create=True, bitmaps=False, target_is_zero=False):
    """
    Arguments:
        unordered_writes (bool): Allow out-of-order writes to the destination.
            This option improves performance, but is only recommended for
            preallocated devices like host devices or other raw block devices.
        create (bool): If True (default) the destination image is created. Must
            be set to False when convert to NBD. If create is False,
            backingFormat, preallocated and dstQcow2Compat are ignored as
            qemu-img ignores them and may return an error at some point
        target_is_zero (bool): If True and using create=False and backing is
            not None, qemu-img convert will not try to zero the target. This is
            required to keep preallocated image preallocated, and improves
            performance. This option is effective only with qemu-img 5.1 and
            later.
    """
    cmd = [_qemuimg.cmd, "convert", "-p", "-t", "none", "-T", "none"]
    options = []
    cwdPath = None

    if srcFormat:
        cmd.extend(("-f", srcFormat))

    if dstFormat:
        cmd.extend(("-O", dstFormat))
        if create:
            if dstFormat == FORMAT.QCOW2:
                qcow2Compat = _validate_qcow2_compat(dstQcow2Compat)
                options.append('compat=' + qcow2Compat)
            if preallocation:
                value = _get_preallocation(preallocation, dstFormat)
                options.append("preallocation=" + value)

    if backing:
        if not os.path.isabs(backing):
            cwdPath = os.path.dirname(srcImage)

        if create:
            options.append('backing_file=' + str(backing))

            if backingFormat:
                options.append('backing_fmt=' + str(backingFormat))
        else:
            cmd.extend(('-B', backing))

    if create:
        if options:
            cmd.extend(('-o', ','.join(options)))
    else:
        cmd.append("-n")
        if backing is None and target_is_zero and target_is_zero_supported():
            cmd.append("--target-is-zero")

    if compressed:
        cmd.append('-c')

    if unordered_writes:
        cmd.append('-W')

    if bitmaps:
        cmd.append('--bitmaps')

    cmd.append(srcImage)
    cmd.append(dstImage)

    return ProgressCommand(cmd, cwd=cwdPath)


def commit(top, topFormat, base=None):
    cmd = [_qemuimg.cmd, "commit", "-p", "-t", "none"]

    if base:
        cmd.extend(("-b", base))
    else:
        # When base volume isn't provided, we add '-d' option in order not
        # to empty the top volume. Emptying the volume may leave the data
        # on the underlying storage. This is critical mainly when volume
        # wipe before delete is required.
        cmd.append("-d")

    cmd.extend(("-f", topFormat))

    cmd.append(top)

    # For simplicity, we always run commit in the image directory.
    workdir = os.path.dirname(top)
    return ProgressCommand(cmd, cwd=workdir)


def map(image):
    cmd = [_qemuimg.cmd, "map", "--output", "json", image]
    # For simplicity, we always run commit in the image directory.
    workdir = os.path.dirname(image)
    out = _run_cmd(cmd, cwd=workdir)
    try:
        return json.loads(out.decode("utf8"))
    except ValueError:
        raise InvalidOutput(cmd, out, "Failed to process qemuimg map output")


def amend(image, compat):
    if compat not in _QCOW2_COMPAT_SUPPORTED:
        raise ValueError("Invalid compat version %r" % compat)

    # For simplicity, we always run commit in the image directory.
    workdir = os.path.dirname(image)
    cmd = [_qemuimg.cmd, "amend", "-o", "compat=" + compat, image]
    _run_cmd(cmd, cwd=workdir)


class ProgressCommand(object):

    REGEXPR = re.compile(br'\s*\(([\d.]+)/100%\)\s*')

    def __init__(self, cmd, cwd=None):
        self._operation = operation.Command(cmd, cwd=cwd)
        self._progress = 0.0

    def run(self):
        out = bytearray()
        for data in self._operation.watch():
            out += data
            self._update_progress(out)

    def abort(self):
        """
        Aborts running operation by sending a termination signal to the
        underlying qemu-img process.

        Note: this is asynchronous operation, returning before the process was
        terminated. You must use wait_for_completion to wait for the underlying
        qemu-img process.

        This method is threadsafe and may be called from any thread.
        """
        self._operation.abort()

    @property
    def progress(self):
        """
        Returns operation progress as float between 0 and 100.

        This method is threadsafe and may be called from any thread.
        """
        return self._progress

    def _update_progress(self, out):
        # Checking the presence of '\r' before splitting will prevent
        # generating the array when it's not needed.
        try:
            idx = out.rindex(b'\r')
        except ValueError:
            return

        # qemu-img updates progress by printing \r (0.00/100%) to standard out.
        # The output could end with a partial progress so we must discard
        # everything after the last \r and then try to parse a progress record.
        valid_progress = out[:idx]
        last_progress = valid_progress.rsplit(b'\r', 1)[-1]

        # No need to keep old progress information around
        del out[:idx + 1]

        m = self.REGEXPR.match(last_progress)
        if m is None:
            raise ValueError('Unable to parse: "%r"' % last_progress)

        self._progress = float(m.group(1))


def resize(image, newSize, format=None):
    cmd = [_qemuimg.cmd, "resize"]

    if format:
        cmd.extend(("-f", format))

    cmd.extend((image, str(newSize)))
    _run_cmd(cmd)


def rebase(image, backing, format=None, backingFormat=None, unsafe=False):
    cmd = [_qemuimg.cmd, "rebase", "-t", "none", "-T", "none"]

    if unsafe:
        cmd.extend(("-u",))

    if format:
        cmd.extend(("-f", format))

    if backingFormat:
        cmd.extend(("-F", backingFormat))

    cmd.extend(("-b", backing, image))

    cwdPath = None if os.path.isabs(backing) else os.path.dirname(image)

    return operation.Command(cmd, cwd=cwdPath)


def compare(img1, img2, img1_format=None, img2_format=None, strict=False):
    cmd = [_qemuimg.cmd, "compare", "-p"]

    if img1_format:
        cmd.extend(('-f', img1_format))

    if img2_format:
        cmd.extend(('-F', img2_format))

    if strict:
        cmd.append("-s")

    cmd.extend([img1, img2])
    cwdPath = os.path.dirname(img1)

    return operation.Command(cmd, cwd=cwdPath)


def bitmap_add(image, bitmap, enable=True, granularity=None):
    cmd = [_qemuimg.cmd, "bitmap", "--add"]

    if enable:
        cmd.append("--enable")
    else:
        cmd.append("--disable")

    cmd.extend([image, bitmap])

    if granularity:
        cmd.extend(("-g", str(granularity)))

    cwdPath = os.path.dirname(image)
    return operation.Command(cmd, cwd=cwdPath)


def bitmap_remove(image, bitmap):
    cmd = [_qemuimg.cmd, "bitmap", "--remove", image, bitmap]

    cwdPath = os.path.dirname(image)
    return operation.Command(cmd, cwd=cwdPath)


def bitmap_merge(src_image, src_bitmap, src_fmt, dst_image, dst_bitmap):
    cmd = [
        _qemuimg.cmd,
        "bitmap",
        "--merge", src_bitmap,
        "-F", src_fmt,
        "-b", src_image,
        dst_image,
        dst_bitmap,
    ]

    cwdPath = os.path.dirname(src_image)
    return operation.Command(cmd, cwd=cwdPath)


def bitmap_update(image, bitmap, enable):
    cmd = [_qemuimg.cmd, "bitmap"]

    if enable:
        cmd.append("--enable")
    else:
        cmd.append("--disable")

    cmd.extend([image, bitmap])

    cwdPath = os.path.dirname(image)
    return operation.Command(cmd, cwd=cwdPath)


# TODO: remove when qemu-kvm >= 5.1 required
# qemu-img 4.2.0-29 have a buggy bitmap command, failing the tests.
@cache.memoized
def bitmaps_supported():
    cmd = [_qemuimg.cmd, "--version"]
    out = _run_cmd(cmd).decode("utf-8")

    # Example output:
    # qemu-img version 4.2.0 (qemu-kvm-4.2.0-29.el8.6)
    # Copyright (c) 2003-2019 Fabrice Bellard and the QEMU Project developers
    match = re.search(r"^qemu-img version (\d)\.(\d)\.(\d) ", out)
    if not match:
        _log.warning("Unexpected output for qemu-img --version: %s", out)
        return False

    try:
        version = [int(x) for x in match.groups()]
    except ValueError:
        _log.warning("Unexpected output for qemu-img --version: %s", out)
        return False

    _log.debug("Detected qemu-img version %s", version)
    return version >= [5, 1, 0]


@cache.memoized
def target_is_zero_supported():
    cmd = [_qemuimg.cmd, "--help"]
    out = _run_cmd(cmd).decode("utf-8")
    return " [--target-is-zero] " in out


def default_qcow2_compat():
    value = config.get('irs', 'qcow2_compat')
    if value not in _QCOW2_COMPAT_SUPPORTED:
        raise exception.InvalidConfiguration(
            reason="Unsupported value for irs:qcow2_compat",
            qcow2_compat=value,
            supported_values=_QCOW2_COMPAT_SUPPORTED)
    return value


def _parse_qemuimg_json(output, expected_type=dict):
    obj = json.loads(output.decode("utf8"))
    if not isinstance(obj, expected_type):
        raise ValueError("Not a %s", expected_type)
    return obj


def _validate_qcow2_compat(value):
    if value is None:
        return default_qcow2_compat()
    if value not in _QCOW2_COMPAT_SUPPORTED:
        raise ValueError("Invalid compat version %r" % value)
    return value


def _get_preallocation(value, format):
    if value not in (PREALLOCATION.OFF,
                     PREALLOCATION.FALLOC,
                     PREALLOCATION.FULL,
                     PREALLOCATION.METADATA):
        raise ValueError("Invalid preallocation type %r" % value)
    if (value == PREALLOCATION.METADATA and
            format not in (FORMAT.QCOW2, FORMAT.QCOW)):
        raise ValueError("Unsupported preallocation mode %r for format %r" %
                         (value, format))
    return value


def _run_cmd(cmd, cwd=None):
    rc, out, err = commands.execCmd(cmd, raw=True, cwd=cwd)
    if rc != 0:
        raise cmdutils.Error(cmd, rc, out, err)
    return out
