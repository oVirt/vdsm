# Copyright 2014-2020 Red Hat, Inc.
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
import os.path
import uuid
import sys

from collections import namedtuple

from vdsm.config import config

from . import NO, MAYBE

from vdsm import cpuinfo
from vdsm.common import cache
from vdsm.common import commands
from vdsm.common import cpuarch
from vdsm.common import pki
from vdsm.common import systemctl
from vdsm.tool import confutils
from vdsm.tool import service
from vdsm.tool.configfile import ParserWrapper
from vdsm import constants


_LIBVIRT_SERVICE_UNIT = "libvirtd.service"
_LIBVIRT_TCP_SOCKET_UNIT = "libvirtd-tcp.socket"
_LIBVIRT_TLS_SOCKET_UNIT = "libvirtd-tls.socket"
_SYSTEMD_REQUIREMENT_PATH_TEMPLATE = "/etc/systemd/system/{}.requires"
_SYSTEMD_UNITS_PATH = "/usr/lib/systemd/system"


requires = frozenset(('certificates',))

services = ("vdsmd", "supervdsmd", "libvirtd")


_LibvirtConnectionConfig = namedtuple(
    "_LibvirtConnectionConfig",
    "auth_tcp, spice_tls")


def configure():
    removeConf()

    vdsmConfiguration = {
        'ssl_enabled': _ssl(),
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

    _inject_unit_requirement(_LIBVIRT_SERVICE_UNIT, _socket_unit())


def validate():
    socket_unit = _socket_unit()

    if socket_unit not in _unit_requirements(_LIBVIRT_SERVICE_UNIT):
        sys.stdout.write("{} doesn't have requirement on {} unit\n".format(
            _LIBVIRT_SERVICE_UNIT, socket_unit))
        return False

    return _validate_config()


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

    if not _socket_unit() in _unit_requirements(_LIBVIRT_SERVICE_UNIT):
        ret = NO

    if ret == MAYBE:
        sys.stdout.write("libvirt is already configured for vdsm\n")
    else:
        sys.stdout.write("libvirt is not configured for vdsm yet\n")
    return ret


def removeConf():
    confutils.remove_conf(FILES, CONF_VERSION)
    _remove_unit_requirements(_LIBVIRT_SERVICE_UNIT, [
        _LIBVIRT_TLS_SOCKET_UNIT, _LIBVIRT_TCP_SOCKET_UNIT
    ])


@cache.memoized
def _ssl():
    return config.getboolean('vars', 'ssl')


@cache.memoized
def _socket_unit():
    return _LIBVIRT_TLS_SOCKET_UNIT if _ssl() else _LIBVIRT_TCP_SOCKET_UNIT


def _inject_unit_requirement(unit, required_unit):
    requirements_dir_name = _SYSTEMD_REQUIREMENT_PATH_TEMPLATE.format(unit)
    os.makedirs(requirements_dir_name, mode=0o755, exist_ok=True)
    try:
        os.symlink(
            os.path.join(_SYSTEMD_UNITS_PATH, required_unit),
            os.path.join(requirements_dir_name, required_unit)
        )
    except FileExistsError:
        pass
    commands.run([systemctl.SYSTEMCTL, "daemon-reload"])


def _remove_unit_requirements(unit, required_units):
    requirements_dir_name = _SYSTEMD_REQUIREMENT_PATH_TEMPLATE.format(unit)

    for required_unit in required_units:
        try:
            os.remove(os.path.join(requirements_dir_name, required_unit))
        except FileNotFoundError:
            pass

    commands.run([systemctl.SYSTEMCTL, "daemon-reload"])


def _unit_requirements(unit_name):
    return systemctl.show(unit_name, ("Requires",))[0]["Requires"]


def _read_libvirt_connection_config():
    lconf_p = ParserWrapper({
        'auth_tcp': 'sasl',
    })
    lconf_p.read(confutils.get_file_path('LCONF', FILES))
    auth_tcp = lconf_p.get('auth_tcp')
    qconf_p = ParserWrapper({'spice_tls': '0'})
    qconf_p.read(confutils.get_file_path('QCONF', FILES))
    spice_tls = qconf_p.getboolean('spice_tls')
    return _LibvirtConnectionConfig(
        auth_tcp, spice_tls)


def _validate_config():
    """
    return True if libvirt configuration files match ssl configuration of
    vdsm.conf.
    """
    cfg = _read_libvirt_connection_config()
    ret = True

    if _ssl():
        if (cfg.auth_tcp != '"none"' and cfg.spice_tls != 0):
            sys.stdout.write(
                "SUCCESS: ssl configured to true. No conflicts\n")
        else:
            sys.stdout.write(
                "FAILED: "
                "conflicting vdsm and libvirt-qemu tls configuration.\n"
                "vdsm.conf with ssl=True "
                "requires the following changes:\n"
                "libvirtd.conf: auth_tcp=\"sasl\"\n"
                "qemu.conf: spice_tls=1.\n"
            )
            ret = False
    else:
        if (cfg.auth_tcp == '"none"' and cfg.spice_tls == 0):
            sys.stdout.write(
                "SUCCESS: ssl configured to false. No conflicts.\n")
        else:
            sys.stdout.write(
                "FAILED: "
                "conflicting vdsm and libvirt-qemu tls configuration.\n"
                "vdsm.conf with ssl=False "
                "requires the following changes:\n"
                "libvirtd.conf: auth_tcp=\"none\"\n"
                "qemu.conf: spice_tls=0.\n"
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


# version != PACKAGE_VERSION since we do not want to update configuration
# on every update. see 'configuration versioning:' at Configfile.py for
# details.
CONF_VERSION = '4.40.0'

LM_CERT_DIR = os.path.join(pki.PKI_DIR, 'libvirt-migrate')
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
                    'migrate_tls_x509_cert_dir': '\"' + LM_CERT_DIR + '\"',
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
                    'DAEMON_COREFILE_LIMIT': 'unlimited',
                    'LIBVIRTD_ARGS': ''
                }

            },
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
