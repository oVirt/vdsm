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
import errno
import os
import uuid
import sys

from vdsm.config import config

from . import \
    InvalidRun, \
    NO, \
    MAYBE
from . certificates import (
    CA_FILE,
    CERT_FILE,
    KEY_FILE,
)
from .. configfile import (
    ConfigFile,
    ParserWrapper,
)
from .. validate_ovirt_certs import validate_ovirt_certs
from ... import utils
from ... import constants

if utils.isOvirtNode():
    from ovirt.node.utils.fs import Config as NodeCfg


requires = frozenset(('certificates',))

services = ("vdsmd", "supervdsmd", "libvirtd")


def _getFile(fname):
    return FILES[fname]['path']


def configure():
    if utils.isOvirtNode():
        if not os.path.exists(constants.P_VDSM_CERT):
            raise InvalidRun(
                "vdsm: Missing certificate, vdsm not registered")
        validate_ovirt_certs()

    # Remove a previous configuration (if present)
    removeConf()

    config.read(_getFile('VDSM_CONF'))
    vdsmConfiguration = {
        'ssl_enabled': config.getboolean('vars', 'ssl'),
        'sanlock_enabled': constants.SANLOCK_ENABLED,
        'libvirt_selinux': constants.LIBVIRT_SELINUX
    }

    # write configuration
    for cfile, content in FILES.items():
        content['configure'](content, vdsmConfiguration)


def validate():
    """
    Validate conflict in configured files
    """
    return _isSslConflict()


def isconfigured():
    """
    Check if libvirt is already configured for vdsm
    """
    ret = MAYBE
    for path in (_getPersistedFiles()):
        if not _openConfig(path).hasConf():
            ret = NO

    if ret == MAYBE:
        sys.stdout.write("libvirt is already configured for vdsm\n")
    else:
        sys.stdout.write("libvirt is not configured for vdsm yet\n")
    return ret


def removeConf():
    for cfile, content in FILES.items():
        content['removeConf'](content['path'])


def _getPersistedFiles():
    """
    get files where vdsm is expected to add a section.
    """
    return [
        cfile['path'] for cfile in FILES.values()
        if cfile['persisted']
    ]


def _isSslConflict():
    """
    return True if libvirt configuration files match ssl configuration of
    vdsm.conf.
    """
    config.read(_getFile('VDSM_CONF'))
    ssl = config.getboolean('vars', 'ssl')

    lconf_p = ParserWrapper({
        'listen_tcp': '0',
        'auth_tcp': 'sasl',
        'listen_tls': '1',
    })
    lconf_p.read(_getFile('LCONF'))
    listen_tcp = lconf_p.getint('listen_tcp')
    auth_tcp = lconf_p.get('auth_tcp')
    listen_tls = lconf_p.getint('listen_tls')
    qconf_p = ParserWrapper({'spice_tls': '0'})
    qconf_p.read(_getFile('QCONF'))
    spice_tls = qconf_p.getboolean('spice_tls')
    ret = True
    if ssl:
        if listen_tls != 0 and listen_tcp != 1 and auth_tcp != '"none"' and \
                spice_tls != 0:
            sys.stdout.write(
                "SUCCESS: ssl configured to true. No conflicts\n")
        else:
            sys.stdout.write(
                "FAILED: "
                "conflicting vdsm and libvirt-qemu tls configuration.\n"
                "vdsm.conf with ssl=True "
                "requires the following changes:\n"
                "libvirtd.conf: listen_tcp=0, auth_tcp=\"sasl\", "
                "listen_tls=1\nqemu.conf: spice_tls=1.\n"
            )
            ret = False
    else:
        if listen_tls == 0 and listen_tcp == 1 and auth_tcp == '"none"' and \
                spice_tls == 0:
            sys.stdout.write(
                "SUCCESS: ssl configured to false. No conflicts.\n")
        else:
            sys.stdout.write(
                "FAILED: "
                "conflicting vdsm and libvirt-qemu tls configuration.\n"
                "vdsm.conf with ssl=False "
                "requires the following changes:\n"
                "libvirtd.conf: listen_tcp=1, auth_tcp=\"none\", "
                "listen_tls=0\n qemu.conf: spice_tls=0.\n"
            )
            ret = False
    return ret


def _isApplicable(fragment, vdsmConfiguration):
        """
        Return true if 'fragment' should be included for current
        configuration. An applicable fragment is a fragment who's list
        of conditions are met according to vdsmConfiguration.
        """
        applyFragment = True
        for key, booleanValue in fragment['conditions'].items():
            if vdsmConfiguration[key] != booleanValue:
                applyFragment = False
        return applyFragment


def _openConfig(path):
    return ConfigFile(path, CONF_VERSION)


def _addSection(content, vdsmConfiguration):
    """
    Add a 'configuration section by vdsm' part to a config file.
    This section contains only keys not originally defined
    The section headers will include the current configuration version.
    """
    configuration = {}
    for fragment in content['fragments']:
        if _isApplicable(fragment, vdsmConfiguration):
            configuration.update(fragment['content'])
    if configuration:
        with _openConfig(content['path']) as conff:
            for key, val in configuration.items():
                conff.addEntry(key, val)


def _removeFile(content, vdsmConfiguration):
    """
    delete a file if it exists.
    """
    if utils.isOvirtNode():
        NodeCfg().delete(content['path'])
    else:
        try:
            os.unlink(content['path'])
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise


def _removeSection(path):
    """
    remove entire 'configuration section by vdsm' section.
    section is removed regardless of it's version.
    """
    if os.path.exists(path):
        with _openConfig(path) as conff:
            conff.removeConf()

# version != PACKAGE_VERSION since we do not want to update configuration
# on every update. see 'configuration versioning:' at Configfile.py for
# details.
CONF_VERSION = '4.17.0'

PKI_DIR = os.path.join(constants.SYSCONF_PATH, 'pki/vdsm')
LS_CERT_DIR = os.path.join(PKI_DIR, 'libvirt-spice')

# be sure to update CONF_VERSION accordingly when updating FILES.
FILES = {

    'VDSM_CONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'vdsm/vdsm.conf'
        ),
        'configure': lambda x, y: True,
        'removeConf': lambda x: True,
        'persisted': False,
    },

    'LCONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'libvirt/libvirtd.conf'
        ),
        'configure': _addSection,
        'removeConf': _removeSection,
        'persisted': True,
        'fragments': [
            {
                'conditions': {},
                'content': {
                    'unix_sock_group': (
                        '"' + constants.QEMU_PROCESS_GROUP + '"'),
                    'unix_sock_rw_perms': '"0770"',
                    'auth_unix_rw': '"sasl"',
                    'host_uuid': '"' + str(uuid.uuid4()) + '"',
                    'keepalive_interval': -1,
                },
            },
            {
                'conditions': {
                    "ssl_enabled": False
                },
                'content': {
                    'auth_tcp': '"none"',
                    'listen_tcp': 1,
                    'listen_tls': 0,
                },

            },
            {
                'conditions': {
                    "ssl_enabled": True,
                },
                'content': {
                    'ca_file': '\"' + CA_FILE + '\"',
                    'cert_file': '\"' + CERT_FILE + '\"',
                    'key_file': '\"' + KEY_FILE + '\"',
                },

            },
        ]
    },

    'QCONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'libvirt/qemu.conf',
        ),
        'configure': _addSection,
        'removeConf': _removeSection,
        'persisted': True,
        'fragments': [
            {
                'conditions': {},
                'content': {
                    'dynamic_ownership': 0,
                    'save_image_format': '"lzop"',
                    'remote_display_port_min': 5900,
                    'remote_display_port_max': 6923,
                    'auto_dump_path': '"/var/log/core"',
                },

            },
            {
                'conditions': {
                    "ssl_enabled": False,
                },
                'content': {
                    'spice_tls': 0,
                },

            },
            {
                'conditions': {
                    "ssl_enabled": True,
                },
                'content': {
                    'spice_tls': 1,
                    'spice_tls_x509_cert_dir': '\"' + LS_CERT_DIR + '\"',
                },

            },
            {
                'conditions': {
                    "libvirt_selinux": False,
                },
                'content': {
                    'security_driver': '"none"',
                },

            },

            {
                'conditions': {
                    "sanlock_enabled": True,
                },
                'content': {
                    'lock_manager': '"sanlock"',
                },

            }
        ]
    },

    'LDCONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'sysconfig/libvirtd',
        ),
        'configure': _addSection,
        'removeConf': _removeSection,
        'persisted': True,
        'fragments': [
            {
                'conditions': {},
                'content': {
                    'LIBVIRTD_ARGS': '--listen',
                    'DAEMON_COREFILE_LIMIT': 'unlimited',
                },

            }]
    },

    'QLCONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'libvirt/qemu-sanlock.conf',
        ),
        'configure': _addSection,
        'removeConf': _removeSection,
        'persisted': True,
        'fragments': [
            {
                'conditions': {
                    "sanlock_enabled": True,
                },
                'content': {
                    'auto_disk_leases': 0,
                    'require_lease_for_disks': 0,
                },

            },
            {
                'conditions': {},
                'content': {
                    'auto_disk_leases': 0,
                    'require_lease_for_disks': 0,
                },

            }
        ]
    },

    'QNETWORK': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'libvirt/qemu/networks/autostart/default.xml',
        ),
        'configure': _removeFile,
        'removeConf': lambda x: True,
        'persisted': False,
    }
}
