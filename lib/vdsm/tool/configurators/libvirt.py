# Copyright 2014-2019 Red Hat, Inc.
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
import os
import uuid
import sys

from collections import namedtuple

from vdsm.config import config

from . import NO, MAYBE

from vdsm import cpuinfo
from vdsm.common import cpuarch
from vdsm.common import pki
from vdsm.common import systemctl
from vdsm.tool import confutils
from vdsm.tool import service
from vdsm.tool.configfile import ParserWrapper
from vdsm import constants


_LIBVIRT_TCP_SOCKET_UNIT = "libvirtd-tcp.socket"
_LIBVIRT_TLS_SOCKET_UNIT = "libvirtd-tls.socket"


requires = frozenset(('certificates',))

services = ("vdsmd", "supervdsmd", "libvirtd")


_LibvirtConnectionConfig = namedtuple(
    "_LibvirtConnectionConfig",
    "auth_tcp, listen_tcp, listen_tls, spice_tls")


def configure():
    # Remove a previous configuration (if present)
    confutils.remove_conf(FILES, CONF_VERSION)

    vdsmConfiguration = {
        'socket_activation': _libvirt_uses_socket_activation(),
        'ssl_enabled': config.getboolean('vars', 'ssl'),
        'sanlock_enabled': constants.SANLOCK_ENABLED,
        'libvirt_selinux': constants.LIBVIRT_SELINUX
    }

    # write configuration
    for cfile, content in FILES.items():
        content['configure'](content, CONF_VERSION, vdsmConfiguration)

    # enable and acivate dev-hugepages1G mounth path
    if not _is_hugetlbfs_1g_mounted():
        try:
            service.service_start('dev-hugepages1G.mount')
        except service.ServiceOperationError:
            status = service.service_status('dev-hugepages1G.mount', False)
            if status == 0:
                raise

    if _libvirt_uses_socket_activation():
        cfg = _read_libvirt_connection_config()

        if cfg.listen_tcp != 0:
            systemctl.enable(_LIBVIRT_TCP_SOCKET_UNIT)

        if cfg.listen_tls != 0:
            systemctl.enable(_LIBVIRT_TLS_SOCKET_UNIT)


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
    for path in (confutils.get_persisted_files(FILES)):
        if not confutils.open_config(path, CONF_VERSION).hasConf():
            ret = NO

    if not _is_hugetlbfs_1g_mounted():
        ret = NO

    if _libvirt_uses_socket_activation():
        cfg = _read_libvirt_connection_config()

        if cfg.listen_tcp != 0 and not _unit_enabled(_LIBVIRT_TCP_SOCKET_UNIT):
            sys.stdout.write("{} unit is disabled\n".format(
                _LIBVIRT_TCP_SOCKET_UNIT))
            ret = NO

        if cfg.listen_tls != 0 and not _unit_enabled(_LIBVIRT_TLS_SOCKET_UNIT):
            sys.stdout.write("{} unit is disabled\n".format(
                _LIBVIRT_TLS_SOCKET_UNIT))
            ret = NO

    if ret == MAYBE:
        sys.stdout.write("libvirt is already configured for vdsm\n")
    else:
        sys.stdout.write("libvirt is not configured for vdsm yet\n")
    return ret


def removeConf():
    confutils.remove_conf(FILES, CONF_VERSION)


def _unit_enabled(unit_name):
    props = systemctl.show(unit_name, ("UnitFileState",))
    return props[0]["UnitFileState"] == "enabled"


def _read_libvirt_connection_config():
    lconf_p = ParserWrapper({
        'listen_tcp': '0',
        'auth_tcp': 'sasl',
        'listen_tls': '1',
    })
    lconf_p.read(confutils.get_file_path('LCONF', FILES))
    listen_tcp = lconf_p.getint('listen_tcp')
    auth_tcp = lconf_p.get('auth_tcp')
    listen_tls = lconf_p.getint('listen_tls')
    qconf_p = ParserWrapper({'spice_tls': '0'})
    qconf_p.read(confutils.get_file_path('QCONF', FILES))
    spice_tls = qconf_p.getboolean('spice_tls')
    return _LibvirtConnectionConfig(
        auth_tcp, listen_tcp, listen_tls, spice_tls)


def _isSslConflict():
    """
    return True if libvirt configuration files match ssl configuration of
    vdsm.conf.
    """
    ssl = config.getboolean('vars', 'ssl')

    cfg = _read_libvirt_connection_config()
    ret = True
    if ssl:
        if (cfg.listen_tls != 0 and cfg.listen_tcp != 1
                and cfg.auth_tcp != '"none"' and cfg.spice_tls != 0):
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
        if (cfg.listen_tls == 0 and cfg.listen_tcp == 1
                and cfg.auth_tcp == '"none"' and cfg.spice_tls == 0):
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


def _is_hugetlbfs_1g_mounted(mtab_path='/etc/mtab'):
    if cpuarch.is_ppc(cpuarch.real()) or 'pdpe1gb' not in cpuinfo.flags():
        return True

    with open(mtab_path, 'r') as f:
        for line in f:
            if '/dev/hugepages1G' in line:
                return True

    return False


def _find_libvirt_socket_units():
    return systemctl.show("libvirtd*.socket", ("Names", "LoadState",))


# https://bugzilla.redhat.com/1750340
# Used in tests
def _libvirt_uses_socket_activation():
    socket_units = _find_libvirt_socket_units()

    if len(socket_units) == 0:
        sys.stdout.write("libvirtd doesn't use systemd socket activation\n")
        return False

    sys.stdout.write("libvirtd socket units status: {}\n".format(socket_units))

    for su in socket_units:
        if su["LoadState"] == "masked":
            sys.stdout.write(("libvirtd doesn't use systemd socket activation"
                              " - one or more of its socket units have been "
                              "masked\n"))
            return False

    sys.stdout.write("libvirtd uses socket activation\n")
    return True


# version != PACKAGE_VERSION since we do not want to update configuration
# on every update. see 'configuration versioning:' at Configfile.py for
# details.
CONF_VERSION = '4.30.0'

LS_CERT_DIR = os.path.join(pki.PKI_DIR, 'libvirt-spice')

# be sure to update CONF_VERSION accordingly when updating FILES.
FILES = {

    'LCONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'libvirt/libvirtd.conf'
        ),
        'configure': confutils.add_section,
        'removeConf': confutils.remove_section,
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
                    'ca_file': '\"' + pki.CA_FILE + '\"',
                    'cert_file': '\"' + pki.CERT_FILE + '\"',
                    'key_file': '\"' + pki.KEY_FILE + '\"',
                },

            },
        ]
    },

    'QCONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'libvirt/qemu.conf',
        ),
        'configure': confutils.add_section,
        'removeConf': confutils.remove_section,
        'persisted': True,
        'fragments': [
            {
                'conditions': {},
                'content': {
                    'dynamic_ownership': 1,
                    'save_image_format': '"gzip"',
                    'user': '"qemu"',
                    'group': '"qemu"',
                    'remote_display_port_min': 5900,
                    'remote_display_port_max': 6923,
                    'max_core': '"unlimited"',
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
        'configure': confutils.add_section,
        'removeConf': confutils.remove_section,
        'persisted': True,
        'fragments': [
            {
                'conditions': {},
                'content': {
                    'DAEMON_COREFILE_LIMIT': 'unlimited'
                }

            },
            {
                'conditions': {
                    'socket_activation': False
                },
                'content': {
                    'LIBVIRTD_ARGS': '--listen'
                }

            }
        ]
    },

    'QLCONF': {
        'path': os.path.join(
            constants.SYSCONF_PATH,
            'libvirt/qemu-sanlock.conf',
        ),
        'configure': confutils.add_section,
        'removeConf': confutils.remove_section,
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
        'configure': confutils.remove_file,
        'removeConf': lambda x, y: True,
        'persisted': False,
    }
}
