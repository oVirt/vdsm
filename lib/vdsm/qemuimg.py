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
import signal
import threading

from vdsm.common import cmdutils as common_cmdutils
from vdsm.common import exception
from vdsm.common.compat import CPopen
from vdsm.storage import operation

from . import cmdutils
from . import utils
from . import commands
from . config import config

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

    return QemuImgOperation(cmd, cwd=cwdPath)


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
    return QemuImgOperation(cmd, cwd=workdir)


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


class QemuImgOperation(object):
    REGEXPR = re.compile(r'\s*\(([\d.]+)/100%\)\s*')

    def __init__(self, cmd, cwd=None):
        self._lock = threading.Lock()
        self._aborted = False
        self._progress = 0.0

        self._stdout = bytearray()
        self._stderr = bytearray()

        self.cmd = cmdutils.wrap_command(
            cmd,
            with_nice=utils.NICENESS.HIGH,
            with_ioclass=utils.IOCLASS.IDLE)
        _log.debug(common_cmdutils.command_log_line(self.cmd, cwd=cwd))
        self._command = CPopen(self.cmd, cwd=cwd,
                               deathSignal=signal.SIGKILL)
        self._stream = utils.CommandStream(
            self._command, self._recvstdout, self._recvstderr)

    def _recvstderr(self, buffer):
        self._stderr += buffer

    def _recvstdout(self, buffer):
        self._stdout += buffer

        # Checking the presence of '\r' before splitting will prevent
        # generating the array when it's not needed.
        try:
            idx = self._stdout.rindex('\r')
        except ValueError:
            return

        # qemu-img updates progress by printing \r (0.00/100%) to standard out.
        # The output could end with a partial progress so we must discard
        # everything after the last \r and then try to parse a progress record.
        valid_progress = self._stdout[:idx]
        last_progress = valid_progress.rsplit('\r', 1)[-1]

        # No need to keep old progress information around
        del self._stdout[:idx + 1]

        m = self.REGEXPR.match(last_progress)
        if m is None:
            raise ValueError('Unable to parse: "%r"' % last_progress)

        self._progress = float(m.group(1))

    @property
    def progress(self):
        """
        Returns operation progress as float between 0 and 100.

        This method is threadsafe and may be called from any thread.
        """
        return self._progress

    @property
    def error(self):
        return str(self._stderr)

    @property
    def finished(self):
        return self._command.poll() is not None

    def poll(self, timeout=None):
        self._stream.receive(timeout=timeout)

        if not self._stream.closed:
            return

        self._command.wait()

        if self._aborted:
            raise exception.ActionStopped()

        common_cmdutils.retcode_log_line(self._command.returncode, self.error)
        if self._command.returncode != 0:
            raise cmdutils.Error(self.cmd, self._command.returncode, "",
                                 self.error)

    def wait_for_completion(self):
        timeout = config.getint("irs", "progress_interval")
        while not self.finished:
            self.poll(timeout)
            _log.debug('qemu-img operation progress: %s%%', self.progress)

    def abort(self):
        """
        Aborts running operation by sending a termination signal to the
        underlying qemu-img process.

        Note: this is asynchronous operation, returning before the process was
        terminated. You must use wait_for_completion to wait for the underlying
        qemu-img process.

        This method is threadsafe and may be called from any thread.
        """
        with self._lock:
            if self._command is None:
                return
            if self._command.poll() is None:
                self._aborted = True
                self._command.terminate()

    def close(self):
        with self._lock:
            self._stream.close()
            self._command = None


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
