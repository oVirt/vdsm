# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
