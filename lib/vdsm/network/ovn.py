# Copyright 2021 Red Hat, Inc.
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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Refer to the README and COPYING files for full details of the license
#

import logging
import os
import pwd

from cryptography import x509
from cryptography.x509.oid import ExtensionOID
from vdsm.network import cmd

SYSTEMCTL = '/usr/bin/systemctl'
OVN_CONTROLLER = 'ovn-controller'
OVSDB_SERVER = 'ovsdb-server'
OVS_IPSEC = 'openvswitch-ipsec'

OVN_CERT_BASE_PATH = '/etc/pki/vdsm/ovn/'
OVN_KEY_PATH = OVN_CERT_BASE_PATH + 'ovn-key.pem'
OVN_CERT_PATH = OVN_CERT_BASE_PATH + 'ovn-cert.pem'
OVN_CA_CERT_PATH = OVN_CERT_BASE_PATH + 'ca-cert.pem'
OVN_USER = 'openvswitch'

OVS_VSCTL = '/usr/bin/ovs-vsctl'


def is_ovn_configured():
    # Check ovn-controller, ovsdb-server and openvswitch-ipsec services
    for service in [OVN_CONTROLLER, OVSDB_SERVER, OVS_IPSEC]:
        if not _is_service_running(service):
            logging.info('The %s service is not running', service)
            return False

    # Check if certificate files exist with correct permissions
    user = pwd.getpwnam(OVN_USER)
    for file in [OVN_KEY_PATH, OVN_CERT_PATH, OVN_CA_CERT_PATH]:
        if not _is_certificate_file_valid(file, user.pw_uid, user.pw_gid):
            logging.info(
                'The certificate file %s is missing '
                'or does not have required permissions',
                file,
            )
            return False

    # Check if the OvS system-id matches at least one SAN DNS in OVN
    # certificate, this is required for IPsec
    ovs_system_id = _get_ovs_system_id()
    cert_san_dns = _get_ovn_cert_san_dns()
    if ovs_system_id not in cert_san_dns:
        logging.info(
            'The OvS system-id (%s) does not match '
            'any of the OVN\'s certificate SAN DNS (%s)',
            ovs_system_id,
            cert_san_dns,
        )
        return False

    return True


def _is_service_running(service):
    rc, _, _ = cmd.exec_sync([SYSTEMCTL, 'is-active', '--quiet', service])
    return rc == 0


def _is_certificate_file_valid(file, uid, gid):
    if not os.path.isfile(file):
        return False

    stat = os.stat(file)
    return stat.st_uid == uid and stat.st_gid == gid


def _get_ovn_cert_san_dns():
    with open(OVN_CERT_PATH, 'rb') as file:
        cert = x509.load_pem_x509_certificate(file.read())
        try:
            san = cert.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            )
            return san.value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            logging.info('Could not locate SAN extension in OVN certificate')
            return []


def _get_ovs_system_id():
    rc, out, err = cmd.exec_sync(
        [OVS_VSCTL, 'get', 'Open_vSwitch', '.', 'external_ids:system-id']
    )
    if rc:
        raise OvSDbException(f'Could not get system-id: {err}')
    return out.strip()


class OvSDbException(Exception):
    pass
