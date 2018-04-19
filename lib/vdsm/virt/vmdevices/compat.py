#
# Copyright 2018 Red Hat, Inc.
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

from vdsm import utils


def device_config(conf):
    for key in ('vm_custom', 'vmid'):
        # added internally by Vdsm. Engine doesn't know or care
        # Easy to detect, safe to remove. We do that to reduce
        # the noise.
        conf.pop(key, None)
    return conf


def interface_config(conf):
    # Reverse action of the conversion in __init__.
    if conf.get('nicModel', '') == 'virtio':
        conf['nicModel'] = 'pv'
    return conf


def drive_config(conf, drive):
    ret_conf = dict_values_to_str(conf)
    # metadata intentionally don't restore it.
    vol_info = getattr(drive, 'volumeInfo', {})
    ret_conf['volumeInfo'] = dict_values_to_str(vol_info)
    # needed by live merge tracking.
    vol_chain = getattr(drive, 'volumeChain', [])
    ret_conf['volumeChain'] = [
        utils.picklecopy(chain_item)
        for chain_item in vol_chain
    ]
    return ret_conf


def dict_values_to_str(d):
    ret = {}
    for key, value in d.items():
        if isinstance(value, int):
            ret[key] = str(value)
        else:
            ret[key] = value
    return ret
