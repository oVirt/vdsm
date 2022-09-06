# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from collections import namedtuple
import glob
import os


def pgrep(name):
    res = []
    for pid in _iteratepids():
        try:
            procName = pidstat(pid).comm
            if procName == name:
                res.append(pid)
        except (OSError, IOError):
            continue
    return res


def _iteratepids():
    for path in glob.iglob("/proc/[0-9]*"):
        pid = os.path.basename(path)
        yield int(pid)


def pidstat(pid):
    res = []
    with open("/proc/%d/stat" % pid, "r") as f:
        statline = f.readline()
        procNameStart = statline.find("(")
        procNameEnd = statline.rfind(")")
        res.append(int(statline[:procNameStart]))
        res.append(statline[procNameStart + 1:procNameEnd])
        args = statline[procNameEnd + 2:].split()
        res.append(args[0])
        res.extend([int(item) for item in args[1:]])
        # Only 44 fields are documented in man page while /proc/pid/stat has 52
        # The rest of the fields contain the process memory layout and
        # exit_code, which are not relevant for our use.
        return _STAT(*res[:len(_STAT._fields)])


_STAT = namedtuple('stat', ('pid', 'comm', 'state', 'ppid', 'pgrp', 'session',
                            'tty_nr', 'tpgid', 'flags', 'minflt', 'cminflt',
                            'majflt', 'cmajflt', 'utime', 'stime', 'cutime',
                            'cstime', 'priority', 'nice', 'num_threads',
                            'itrealvalue', 'starttime', 'vsize', 'rss',
                            'rsslim', 'startcode', 'endcode', 'startstack',
                            'kstkesp', 'kstkeip', 'signal', 'blocked',
                            'sigignore', 'sigcatch', 'wchan', 'nswap',
                            'cnswap', 'exit_signal', 'processor',
                            'rt_priority', 'policy', 'delayacct_blkio_ticks',
                            'guest_time', 'cguest_time'))
