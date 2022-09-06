# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import six

from vdsm.common import cache


# This function gets dict and returns new dict that includes only string
# value for each key. Keys in d that their value is a dictionary will be
# ignored because those keys define a lable for the sub dictionary
# (and those keys are irrelevant for us in dmidecode output)
def __leafDict(d):
    ret = {}
    for k, v in six.iteritems(d):
        if isinstance(v, dict):
            ret.update(__leafDict(v))
        else:
            ret[k] = v
    return ret


@cache.memoized
def getSystemInfo():
    # pylint: disable=no-member
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
            sysStruct[('system' + k).replace(' ', '')] = val.decode('utf-8')

    return sysStruct
