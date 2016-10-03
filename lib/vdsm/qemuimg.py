#
# Copyright 2012-2016 Red Hat, Inc.
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

from vdsm.common import exception
from . import utils
from . import cmdutils
from . import commands
from . import procwatch
from .compat import CPopen
from . config import config

_qemuimg = utils.CommandPath("qemu-img",
                             "/usr/bin/qemu-img",)  # Fedora

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
    cmd = [_qemuimg.cmd, "info", "--output", "json"]

    if format:
        cmd.extend(("-f", format))

    cmd.append(image)
    rc, out, err = commands.execCmd(cmd, raw=True)
    if rc != 0:
        raise QImgError(rc, out, err)

    try:
        qemu_info = _parse_qemuimg_json(out)
    except ValueError:
        raise QImgError(rc, out, err, "Failed to process qemu-img output")

    try:
        info = {
            'format': qemu_info['format'],
            'virtualsize': qemu_info['virtual-size'],
        }
    except KeyError as key:
        raise QImgError(rc, out, err, "Missing field: %r" % key)

    if 'cluster-size' in qemu_info:
        info['clustersize'] = qemu_info['cluster-size']
    if 'backing-filename' in qemu_info:
        info['backingfile'] = qemu_info['backing-filename']
    if qemu_info['format'] == FORMAT.QCOW2:
        try:
            info['compat'] = qemu_info['format-specific']['data']['compat']
        except KeyError:
            raise QImgError(rc, out, err, "'compat' expected but not found")

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

    rc, out, err = commands.execCmd(cmd, cwd=cwdPath)

    if rc != 0:
        raise QImgError(rc, out, err)


def check(image, format=None):
    cmd = [_qemuimg.cmd, "check", "--output", "json"]

    if format:
        cmd.extend(("-f", format))

    cmd.append(image)
    rc, out, err = commands.execCmd(cmd, raw=True)

    # FIXME: handle different error codes and raise errors accordingly
    if rc != 0:
        raise QImgError(rc, out, err)

    try:
        qemu_check = _parse_qemuimg_json(out)
    except ValueError:
        raise QImgError(rc, out, err, "Failed to process qemu-img output")
    try:
        return {"offset": qemu_check["image-end-offset"]}
    except KeyError:
        raise QImgError(rc, out, err, "unable to parse qemu-img check output")


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


class QemuImgOperation(object):
    REGEXPR = re.compile(r'\s*\(([\d.]+)/100%\)\s*')

    def __init__(self, cmd, cwd=None):
        self._aborted = False
        self._progress = 0.0

        self._stdout = bytearray()
        self._stderr = bytearray()

        cmd = cmdutils.wrap_command(cmd, with_nice=utils.NICENESS.HIGH,
                                    with_ioclass=utils.IOCLASS.IDLE)
        _log.debug(cmdutils.command_log_line(cmd, cwd=cwd))
        self._process = CPopen(cmd, cwd=cwd, deathSignal=signal.SIGKILL)
        self._watcher = procwatch.ProcessWatcher(
            self._process, self._recvstdout, self._recvstderr)

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
        return self._progress

    @property
    def error(self):
        return str(self._stderr)

    @property
    def finished(self):
        return self._process.poll() is not None

    def poll(self, timeout=None):
        self._watcher.receive(timeout=timeout)

        if not self._watcher.closed:
            return

        self._process.wait()

        if self._aborted:
            raise exception.ActionStopped()

        cmdutils.retcode_log_line(self._process.returncode, self.error)
        if self._process.returncode != 0:
            raise QImgError(self._process.returncode, "", self.error)

    def wait_for_completion(self):
        timeout = config.getint("irs", "progress_interval")
        while not self.finished:
            self.poll(timeout)
            _log.debug('qemu-img operation progress: %s%%', self.progress)

    def abort(self):
        if self._process.poll() is None:
            self._aborted = True
            self._process.terminate()


def resize(image, newSize, format=None):
    cmd = [_qemuimg.cmd, "resize"]

    if format:
        cmd.extend(("-f", format))

    cmd.extend((image, str(newSize)))
    rc, out, err = commands.execCmd(cmd)

    if rc != 0:
        raise QImgError(rc, out, err)


def rebase(image, backing, format=None, backingFormat=None, unsafe=False,
           stop=None):
    cmd = [_qemuimg.cmd, "rebase", "-t", "none", "-T", "none"]

    if unsafe:
        cmd.extend(("-u",))

    if format:
        cmd.extend(("-f", format))

    if backingFormat:
        cmd.extend(("-F", backingFormat))

    cmd.extend(("-b", backing, image))

    cwdPath = None if os.path.isabs(backing) else os.path.dirname(image)
    rc, out, err = commands.watchCmd(
        cmd, cwd=cwdPath, stop=stop, nice=utils.NICENESS.HIGH,
        ioclass=utils.IOCLASS.IDLE)

    if rc != 0:
        raise QImgError(rc, out, err)


def default_qcow2_compat():
    value = config.get('irs', 'qcow2_compat')
    if value not in _QCOW2_COMPAT_SUPPORTED:
        raise exception.InvalidConfiguration(
            "Unsupported value for irs:qcow2_compat: %r" % value)
    return value


def _parse_qemuimg_json(output):
    obj = json.loads(output)
    if not isinstance(obj, dict):
        raise ValueError("not a JSON object")
    return obj


def _validate_qcow2_compat(value):
    if value is None:
        return default_qcow2_compat()
    if value not in _QCOW2_COMPAT_SUPPORTED:
        raise ValueError("Invalid compat version %r" % value)
    return value
