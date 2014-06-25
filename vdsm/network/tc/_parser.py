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


def linearize(inp):
    """Generator of tc entries (that can span over multiple textual lines).
    Each entry is a """
    current = []
    for line in inp:
        if line.startswith(' ') or line.startswith('\t'):
            current.append(LINE_DELIMITER)
            current.extend(line.strip().split())
        else:
            if current:
                yield current
            current = line.strip().split()
    if current:
        yield current
