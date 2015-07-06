# Copyright 2017 Red Hat, Inc.
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
import sys
import os

from . import MAYBE, NO
from vdsm.tool import confutils
from vdsm import constants

CONF_VERSION = '4.20.0'


def configure():
    confutils.remove_conf(FILES, CONF_VERSION)
    for conf_file, content in FILES.items():
        content['configure'](content, CONF_VERSION)


def isconfigured():
    """
    Check if abrt is already configured for vdsm
    """
    ret = MAYBE

    for path in (confutils.get_persisted_files(FILES)):
        if not confutils.open_config(path, CONF_VERSION).hasConf():
            ret = NO

    if ret == MAYBE:
        sys.stdout.write("abrt is already configured for vdsm\n")
    else:
        sys.stdout.write("abrt is not configured for vdsm\n")
    return ret


FILES = {
    'ABRT_CONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'abrt/abrt.conf'
        ),
        'configure': confutils.add_section,
        'removeConf': confutils.remove_section,
        'persisted': True,
        'fragments': [
            {
                'conditions': {},
                'content': {
                    'DumpLocation': '/var/tmp/abrt',
                    'AutoreportingEvent': 'report_uReport',
                    'MaxCrashReportsSize': '1000',
                    'AutoreportingEnabled': 'yes'
                },
            },
        ]
    },
    'CCPP_CONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'abrt/plugins/CCpp.conf'
        ),
        'configure': confutils.add_section,
        'removeConf': confutils.remove_section,
        'persisted': True,
        'fragments': [
            {
                'conditions': {},
                'content': {
                    'MakeCompatCore': 'no',
                    'SaveBinaryImage': 'no',
                    'CreateCoreBacktrace': 'yes',
                    'SaveFullCore': 'no',
                },
            },
        ]
    },
    'VMCORE_CONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'abrt/plugins/vmcore.conf'
        ),
        'configure': confutils.add_section,
        'removeConf': confutils.remove_section,
        'persisted': True,
        'fragments': [
            {
                'conditions': {},
                'content': {
                    'CopyVMcore': 'no',
                },
            },
        ]
    },
    'PKG_CONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'abrt/abrt-action-save-package-data.conf'
        ),
        'configure': confutils.add_section,
        'removeConf': confutils.remove_section,
        'persisted': True,
        'fragments': [
            {
                'conditions': {},
                'content': {
                    'OpenGPGCheck': 'no'
                },
            },
        ]
    }
}
