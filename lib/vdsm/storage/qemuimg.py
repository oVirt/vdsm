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

from vdsm import cmdutils
from vdsm import commands
from vdsm.common import cmdutils as common_cmdutils
from vdsm.common import exception
from vdsm.config import config
from vdsm.storage import operation

_qemuimg = common_cmdutils.CommandPath("qemu-img", "/usr/bin/qemu-img")

_log = logging.getLogger("QemuImg")


class FORMAT:
    QCOW2 = "qcow2"
    QCOW = "qcow"
    QED = "qed"
    RAW = "raw"
    VMDK = "vmdk"

_QCOW2_COMPAT_SUPPORTED = ("0.10", "1.1")


def supports_compat(compat):
    return compat in _QCOW2_COMPAT_SUPPORTED


class InvalidOutput(cmdutils.Error):
    msg = ("Commmand {self.cmd} returned invalid output: {self.out}: "
           "{self.reason}")

    def __init__(self, cmd, out, reason):
        self.cmd = cmd
        self.out = out
        self.reason = reason


def info(image, format=None):
    cmd = [_qemuimg.cmd, "info", "--output", "json"]

    if format:
        cmd.extend(("-f", format))

    cmd.append(image)
    out = _run_cmd(cmd)

    try:
        qemu_info = _parse_qemuimg_json(out)
    except ValueError:
        raise InvalidOutput(cmd, out, "Failed to process qemu-img output")

    try:
        info = {
            'format': qemu_info['format'],
            'virtualsize': qemu_info['virtual-size'],
        }
    except KeyError as key:
        raise InvalidOutput(cmd, out, "Missing field: %r" % key)

    if 'cluster-size' in qemu_info:
        info['clustersize'] = qemu_info['cluster-size']
    if 'backing-filename' in qemu_info:
        info['backingfile'] = qemu_info['backing-filename']
    if qemu_info['format'] == FORMAT.QCOW2:
        try:
            info['compat'] = qemu_info['format-specific']['data']['compat']
        except KeyError:
            raise InvalidOutput(cmd, out, "'compat' expected but not found")

    return info


def create(image, size=None, format=None, qcow2Compat=None,
           backing=None, backingFormat=None):
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

    cmd.append(image)

    if size is not None:
        cmd.append(str(size))

    _run_cmd(cmd, cwd=cwdPath)


def check(image, format=None):
    cmd = [_qemuimg.cmd, "check", "--output", "json"]

    if format:
        cmd.extend(("-f", format))

    cmd.append(image)
    out = _run_cmd(cmd)

    try:
        qemu_check = _parse_qemuimg_json(out)
    except ValueError:
        raise InvalidOutput(cmd, out, "Failed to process qemu-img output")
    try:
        return {"offset": qemu_check["image-end-offset"]}
    except KeyError:
        raise InvalidOutput(cmd, out, "unable to parse qemu-img check output")


def convert(srcImage, dstImage, srcFormat=None, dstFormat=None,
            dstQcow2Compat=None, backing=None, backingFormat=None):
    cmd = [_qemuimg.cmd, "convert", "-p", "-t", "none", "-T", "none"]
    options = []
    cwdPath = None

    if srcFormat:
        cmd.extend(("-f", srcFormat))

    cmd.append(srcImage)

    if dstFormat:
        cmd.extend(("-O", dstFormat))
        if dstFormat == FORMAT.QCOW2:
            qcow2Compat = _validate_qcow2_compat(dstQcow2Compat)
            options.append('compat=' + qcow2Compat)

    if backing:
        if not os.path.isabs(backing):
            cwdPath = os.path.dirname(srcImage)

        options.append('backing_file=' + str(backing))

        if backingFormat:
            options.append('backing_fmt=' + str(backingFormat))

    if options:
        cmd.extend(('-o', ','.join(options)))

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

    REGEXPR = re.compile(r'\s*\(([\d.]+)/100%\)\s*')

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
            idx = out.rindex('\r')
        except ValueError:
            return

        # qemu-img updates progress by printing \r (0.00/100%) to standard out.
        # The output could end with a partial progress so we must discard
        # everything after the last \r and then try to parse a progress record.
        valid_progress = out[:idx]
        last_progress = valid_progress.rsplit('\r', 1)[-1]

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


def default_qcow2_compat():
    value = config.get('irs', 'qcow2_compat')
    if value not in _QCOW2_COMPAT_SUPPORTED:
        raise exception.InvalidConfiguration(
            "Unsupported value for irs:qcow2_compat: %r" % value)
    return value


def _parse_qemuimg_json(output):
    obj = json.loads(output.decode("utf8"))
    if not isinstance(obj, dict):
        raise ValueError("not a JSON object")
    return obj


def _validate_qcow2_compat(value):
    if value is None:
        return default_qcow2_compat()
    if value not in _QCOW2_COMPAT_SUPPORTED:
        raise ValueError("Invalid compat version %r" % value)
    return value


def _run_cmd(cmd, cwd=None):
    rc, out, err = commands.execCmd(cmd, raw=True, cwd=cwd)
    if rc != 0:
        raise cmdutils.Error(cmd, rc, out, err)
    return out
