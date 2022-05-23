# -*- coding: utf-8 -*-
# Copyright 2014 Red Hat, Inc.
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
from __future__ import division

from vdsm.common.units import KiB, MiB

LINE_DELIMITER = 0


class TCParseError(Exception):
    pass


def consume(tokens, *expected):
    found = next(tokens)
    if found not in expected:
        raise TCParseError('Found %s, expected %s' % (found, expected))


def parse_skip_line(tokens):
    """Consumes tokens until it finds an end of line marking '\0'"""
    for token in tokens:
        if token == LINE_DELIMITER:
            break


def parse_rate(tokens):
    """Returns a numerical bit representation of the textual rate in tokens"""
    rate = next(tokens)
    if rate[-5:] == 'Gibit':
        return int(float(rate[:-5]) * 1024**3)
    elif rate[-5:] == 'Mibit':
        return int(float(rate[:-5]) * 1024**2)
    elif rate[-5:] == 'Kibit':
        return int(float(rate[:-5]) * 1024)
    elif rate[-4:] == 'Gbit':
        return int(float(rate[:-4]) * 1000**3)
    elif rate[-4:] == 'Mbit':
        return int(float(rate[:-4]) * 1000**2)
    elif rate[-4:] == 'Kbit':
        return int(float(rate[:-4]) * 1000)
    else:
        return int(float(rate[:-3]))


def parse_size(tokens):
    """Returns a numerical byte representation of the textual size in tokens"""
    size = next(tokens)
    if size[-2:] == 'Mb':
        return int(float(size[:-2]) * MiB)
    elif size[-2:] == 'Kb':
        return int(float(size[:-2]) * KiB)
    else:  # bytes
        return int(size[:-1])


def parse_time(tokens):
    """Returns a numerical µs representation of the textual size in tokens"""
    size = next(tokens)
    if size[-2:] == 'ms':
        return int(float(size[:-2]) * 10**3)
    elif size[-2:] == 'us':
        return int(size[:-2])
    else:  # s
        return int(float(size[:-1]) * 10**6)


def parse_int(tokens, base=10):
    return int(next(tokens), base)


def parse_true(_):
    return True


def parse_float(tokens):
    return float(next(tokens))


def parse_sec(tokens):
    return int(next(tokens)[:-3])  # Swallow trailing 'sec'


def parse_str(tokens):
    return next(tokens)


def parse_hex(tokens):
    return parse_int(tokens, 16)


def linearize(inp):
    """Generator of tc entries (that can span over multiple textual lines).
    Each entry is a"""
    current = []
    for line in inp:
        if not line.strip():
            continue
        if line.startswith(' ') or line.startswith('\t'):
            current.append(LINE_DELIMITER)
            current.extend(line.strip().split())
        else:
            if current:
                yield current
            current = line.strip().split()
    if current:
        yield current
