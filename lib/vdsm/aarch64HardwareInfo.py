# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os.path
import subprocess

from vdsm import cpuinfo
from vdsm.common import cache



def get_sys_info():

    cmd = 'dmidecode -t system'
    sys_info = {}
    try:
        output = subprocess.check_output(cmd, shell=True, universal_newlines=True)
        for item in output.split("\n"):
            if 'Manufacturer' in item or \
                'Product Name' in item or \
                'Version' in item or \
                'Serial Number' in item or \
                'UUID' in item or \
                'Family' in item :
                item = item.strip()
                key = item.split(":")[0].strip()
                value = item.split(":")[1].strip()
                sys_info[key]=value

    except:
        print("ERROR")

    return sys_info




@cache.memoized
def getHardwareInfoStructure():
    sys_info_dict=get_sys_info()


    return {
        'systemSerialNumber': sys_info_dict.get('Serial Number', 'unavailable'),
        'systemFamily': sys_info_dict.get('Family', 'unavailable'),
        'systemVersion': sys_info_dict.get('Version', 'unavailable'),
        'systemUUID': sys_info_dict.get('UUID', 'unavailable'),
        'systemProductName': sys_info_dict.get('Product Name', 'unavailable'),
        'systemManufacturer': sys_info_dict.get('Manufacturer', 'unavailable'),
    }

