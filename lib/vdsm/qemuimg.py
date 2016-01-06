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

from __future__ import absolute_import
import logging
import os
import re
import signal

from cpopen import CPopen

from . import utils
from . import cmdutils
from . import commands

_qemuimg = utils.CommandPath("qemu-img",
                             "/usr/bin/qemu-img",)  # Fedora, EL6

_log = logging.getLogger("QemuImg")


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

# The first row of qemu-img info output where optional fields may appear
_INFO_OPTFIELDS_STARTIDX = 4

# The first row of qemu-img check output where the 'offset' may appear
_CHECK_OPTFIELDS_STARTIDX = 1


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
    rc, out, err = commands.execCmd(cmd, deathSignal=signal.SIGKILL)

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
    row = _INFO_OPTFIELDS_STARTIDX
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
        if format == FORMAT.QCOW2 and _supports_qcow2_compat('create'):
            cmd.extend(('-o', 'compat=' + QCOW2_COMPAT))

    if backing:
        if not os.path.isabs(backing):
            cwdPath = os.path.dirname(image)
        cmd.extend(("-b", backing))

    if backingFormat:
        cmd.extend(("-F", backingFormat))

    cmd.append(image)

    if size is not None:
        cmd.append(str(size))

    rc, out, err = commands.execCmd(cmd, cwd=cwdPath,
                                    deathSignal=signal.SIGKILL)

    if rc != 0:
        raise QImgError(rc, out, err)


def check(image, format=None):
    cmd = [_qemuimg.cmd, "check"]

    if format:
        cmd.extend(("-f", format))

    cmd.append(image)
    rc, out, err = commands.execCmd(cmd, deathSignal=signal.SIGKILL)

    # FIXME: handle different error codes and raise errors accordingly
    if rc != 0:
        raise QImgError(rc, out, err)
    # Scan for 'offset' in the output
    for row in range(_CHECK_OPTFIELDS_STARTIDX, len(out)):
        try:
            check = {
                'offset': int(__iregexSearch("offset", out[row]))
            }
            return check
        except _RegexSearchError:
            pass
        except:
            break
    raise QImgError(rc, out, err, "unable to parse qemu-img check output")


def convert(srcImage, dstImage, srcFormat=None, dstFormat=None,
            backing=None, backingFormat=None):
    cmd = [_qemuimg.cmd, "convert", "-p", "-t", "none"]
    options = []
    cwdPath = None

    if _supports_src_cache('convert'):
        cmd.extend(("-T", "none"))

    if srcFormat:
        cmd.extend(("-f", srcFormat))

    cmd.append(srcImage)

    if dstFormat:
        cmd.extend(("-O", dstFormat))
        if dstFormat == FORMAT.QCOW2 and _supports_qcow2_compat('convert'):
            options.append('compat=' + QCOW2_COMPAT)

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
        self._command = CPopen(cmd, cwd=cwd, deathSignal=signal.SIGKILL)
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
        return self._progress

    @property
    def error(self):
        return str(self._stderr)

    @property
    def finished(self):
        return self._command.poll() is not None

    def wait(self, timeout=None):
        self._stream.receive(timeout=timeout)

        if not self._stream.closed:
            return

        self._command.wait()

        if self._aborted:
            raise utils.ActionStopped()

        cmdutils.retcode_log_line(self._command.returncode, self.error)
        if self._command.returncode != 0:
            raise QImgError(self._command.returncode, "", self.error)

    def abort(self):
        if self._command.poll() is None:
            self._aborted = True
            self._command.terminate()


def resize(image, newSize, format=None):
    cmd = [_qemuimg.cmd, "resize"]

    if format:
        cmd.extend(("-f", format))

    cmd.extend((image, str(newSize)))
    rc, out, err = commands.execCmd(cmd, deathSignal=signal.SIGKILL)

    if rc != 0:
        raise QImgError(rc, out, err)


def rebase(image, backing, format=None, backingFormat=None, unsafe=False,
           stop=None):
    cmd = [_qemuimg.cmd, "rebase", "-t", "none"]

    if _supports_src_cache('rebase'):
        cmd.extend(("-T", "none"))

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


# Testing capabilities

def _supports_qcow2_compat(command):
    """
    qemu-img "create" and "convert" commands support a "compat" option in
    recent versions. This will run the specified command using the "-o ?"
    option to find if "compat" option is available.

    Raises KeyError if called with another command.

    TODO: Remove this when qemu versions providing the "compat" option are
    available on all platforms.
    """
    # Older qemu-img requires all filenames although unneeded
    args = {"create": ("-f", ("/dev/null",)),
            "convert": ("-O", ("/dev/null", "/dev/null"))}
    flag, dummy_files = args[command]

    cmd = [_qemuimg.cmd, command, flag, FORMAT.QCOW2, "-o", "?"]
    cmd.extend(dummy_files)

    rc, out, err = commands.execCmd(cmd, raw=True)

    if rc != 0:
        raise QImgError(rc, out, err)

    # Supported options:
    # compat           Compatibility level (0.10 or 1.1)

    return '\ncompat ' in out


@utils.memoized
def _supports_src_cache(command):
    """
    The "-T" option specifies the cache mode that should be used with the
    source file. This will check if "-T" option is available, aiming to set it
    to "none", avoiding the use of cache memory (BZ#1138690).
    """
    # REQUIRED_FOR: FEDORA 20 (no qemu-img with -T support)
    cmd = [_qemuimg.cmd, "--help"]
    rc, out, err = commands.execCmd(cmd, raw=True)

    # REQUIRED_FOR: EL6 (--help returns 1)
    if rc not in (0, 1):
        raise QImgError(rc, out, err)

    # Line to match:
    #   convert [-c] [-p] [-q] [-n] [-f fmt] [-t cache] [-T src_cache]...
    pattern = r"\n +%s .*\[-T src_cache\]" % command
    return re.search(pattern, out) is not None
