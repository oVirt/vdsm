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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

from vdsm.common import cache


# This function gets dict and returns new dict that includes only string
# value for each key. Keys in d that their value is a dictionary will be
# ignored because those keys define a lable for the sub dictionary
# (and those keys are irrelevant for us in dmidecode output)
def __leafDict(d):
    ret = {}
    for k, v in d.iteritems():
        if isinstance(v, dict):
            ret.update(__leafDict(v))
        else:
            ret[k] = v
    return ret


@cache.memoized
def getSystemInfo():
    import dmidecode

    return __leafDict(dmidecode.system())


@cache.memoized
def getHardwareInfoStructure():
    dmiInfo = getSystemInfo()
    sysStruct = {}
    for k in ('Manufacturer', 'Product Name', 'Version', 'Serial Number',
              'UUID', 'Family'):
        val = dmiInfo.get(k, None)
        if val not in [None, 'Not Specified']:
            sysStruct[('system' + k).replace(' ', '')] = val

    return sysStruct
