# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import argparse
import getpass
import hashlib
import logging
import os
import pwd
import ssl
import sys
import tempfile

import requests
import selinux

from . import expose

from vdsm import host
from vdsm.common.conv import tobool


class Register(object):

    def __init__(self, engine_fqdn, engine_https_port=None,
                 fingerprint=None, ssh_port=None,
                 ssh_user=None, check_fqdn=True,
                 vdsm_port=None, node_address=None,
                 vdsm_uuid=None, node_name=None):
        """
        Attributes:

        engine_fqdn       - Engine FQDN or IP address
        engine_https_port - Engine https port
        fingeprint        - Fingerprint to be validated
        ssh_user          - SSH user that will establish the connection
                            from Engine
        ssh_port          - Port of ssh daemon is running
        check_fqdn        - Validate Engine FQDN against CA (True or False)
                            Default is TRUE
        vdsm_port         - VDSM listen port
        node_address      - Specify node address or FQDN
        node_name         - Specify node name
        vdsm_uuid         - Provide host UUID to be used instead vdsm.utils.
                            Useful for hosts with blank or buggy DMI
        """
        self.logger = self._set_logger()
        self.logger.debug("=======================================")
        self.logger.debug("Registering the node")
        self.logger.debug("=======================================")
        self.logger.debug("Received the following attributes:")

        self.engine_fqdn = engine_fqdn
        self.logger.debug("Engine FQDN: {fqdn}".format(fqdn=self.engine_fqdn))

        self.engine_url = "https://{e}".format(e=engine_fqdn)
        if engine_https_port is None:
            self.engine_port = "443"
        else:
            self.engine_port = engine_https_port
            self.engine_url = "https://{e}:{p}".format(e=self.engine_fqdn,
                                                       p=self.engine_port)

        self.logger.debug("Engine URL: {url}".format(url=self.engine_url))
        self.logger.debug("Engine https port: {hp}".format(
                          hp=self.engine_port))

        if check_fqdn is None:
            self.check_fqdn = True
        else:
            self.check_fqdn = tobool(check_fqdn)
        self.logger.debug("Check FQDN: {v}".format(v=self.check_fqdn))

        self.fprint = fingerprint
        self.logger.debug("Fingerprint: {fp}".format(fp=self.fprint))

        self.node_address = node_address
        self.logger.debug("Node address: {nf}".format(nf=self.node_address))

        self.node_name = node_name
        self.logger.debug("Node name: {na}".format(na=self.node_name))

        if ssh_user is None:
            self.ssh_user = getpass.getuser()
        else:
            self.ssh_user = ssh_user
        self.logger.debug("SSH User: {su}".format(su=self.ssh_user))

        if ssh_port is None:
            self.ssh_port = "22"
        else:
            self.ssh_port = ssh_port
        self.logger.debug("SSH Port: {sp}".format(sp=self.ssh_port))

        if vdsm_port is None:
            self.vdsm_port = "54321"
        else:
            self.vdsm_port = vdsm_port
        self.logger.debug("VDSM Port: {sp}".format(sp=self.vdsm_port))

        self.vdsm_uuid = vdsm_uuid
        self.logger.debug("VDSM UUID: {uuid_provided}".format(
                          uuid_provided=self.vdsm_uuid))

        self.ca_dir = "/etc/pki/ovirt-engine/"
        self.ca_engine = "{d}{f}".format(d=self.ca_dir, f="ca.pem")
        self.logger.debug("Engine CA: {ca}".format(ca=self.ca_engine))

    def handshake(self):
        """
        Initial communication with Engine to validate
        the registration.
        """

        self.logger.info("Starting registration...")

        ucmd = "/ovirt-engine/services/host-register?version=1&command="
        __GET_VERSION = "https://{e}{u}{c}".format(e=self.engine_fqdn,
                                                   u=ucmd,
                                                   c="get-version")

        self.logger.debug("Get version via: {0}".format(__GET_VERSION))

        res = requests.get(__GET_VERSION, verify=False)
        if res.status_code != 200:
            raise RuntimeError("Cannot get registration version from Engine!")

        self.url_CA = "{e}{uc}{c}".format(e=self.engine_url,
                                          uc=ucmd,
                                          c="get-pki-trust")

        self.url_ssh_key = "{e}{uc}{c}".format(e=self.engine_url,
                                               uc=ucmd,
                                               c="get-ssh-trust")

        ureg = "{uc}register&sshUser={sshu}&" \
               "sshPort={sshp}&port={mp}".format(uc=ucmd,
                                                 sshu=self.ssh_user,
                                                 sshp=self.ssh_port,
                                                 mp=self.vdsm_port)

        if self.node_name is not None:
            ureg += "&name={name}".format(name=self.node_name)

        if self.node_address is not None:
            ureg += "&address={addr}".format(addr=self.node_address)

        self.url_reg = "{e}{u}".format(e=self.engine_url, u=ureg)

        self.logger.debug("Download CA via: {u}".format(u=self.url_CA))
        self.logger.debug("Download SSH via: {u}".format(u=self.url_ssh_key))

    def _set_logger(self):
        """
        The logging settings
        Saving log in: /var/log/vdsm/register.log
        """
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        fh = logging.FileHandler("/var/log/vdsm/register.log")
        fh.setLevel(logging.DEBUG)
        debug_fmt = logging.Formatter("%(asctime)s %(message)s",
                                      "%m/%d/%Y %I:%M:%S %p")

        ih = logging.StreamHandler(stream=sys.stdout)
        ih.setLevel(logging.INFO)
        info_fmt = logging.Formatter("%(message)s",
                                     "%m/%d/%Y %I:%M:%S %p")

        fh.setFormatter(debug_fmt)
        ih.setFormatter(info_fmt)

        logger.addHandler(fh)
        logger.addHandler(ih)

        logging.captureWarnings(True)

        return logger

    def _execute_http_request(self, url, cert_validation=True):
        """
        Execute http requests
        url -- URL to be requested
        cert_validation -- SSL cert will be verified

        Returns: Content of http request
        """
        if self.check_fqdn and cert_validation:
            cert_validation = self.ca_engine
        else:
            cert_validation = False

        res = requests.get("{u}".format(u=url), verify=cert_validation)
        if res.status_code != 200:
            raise requests.RequestException(
                "http response was non OK, code {r}".format(r=res.status_code)
            )

        return res.content

    def _silent_restorecon(self, path):
        """
        Execute selinux restorecon cmd to determined file

        Args
        path -- full path to file
        """

        try:
            selinux.restorecon(path)
        except:
            self.logger.error("restorecon %s failed" % path, exc_info=True)

    def _calculate_fingerprint(self, cert):
        """
        Calculate fingerprint of certificate

        Args
        cert -- certificate file to be calculated the fingerprint

        Returns
        The fingerprint
        """

        with open(cert, 'r') as f:
            cert = f.read()

        fp = hashlib.sha1(ssl.PEM_cert_to_DER_cert(cert)).hexdigest()
        fp = ':'.join(fp[pos:pos + 2] for pos in range(0, len(fp), 2))

        return fp

    def host_uuid(self):
        """
        Determine host UUID and if there is no existing /etc/vdsm/vdsm.id
        it will genereate UUID and save/persist in /etc/vdsm/vdsm.id
        """

        if self.vdsm_uuid:
            self.uuid = self.vdsm_uuid
        else:
            self.uuid = host.uuid()

        self.url_reg += "&uniqueId={u}".format(u=self.uuid)

        self.logger.debug("Registration via: {u}".format(u=self.url_reg))

        __VDSM_ID = "/etc/vdsm/vdsm.id"

        if self.vdsm_uuid and os.path.exists(__VDSM_ID):
            os.unlink(__VDSM_ID)

        if not os.path.exists(__VDSM_ID):
            with open(__VDSM_ID, 'w') as f:
                f.write(self.uuid)

        self.logger.info("Host UUID: {u}".format(u=self.uuid))

    def download_ca(self):
        """
        Download CA from Engine and save self.ca_engine
        """
        self.logger.info("Collecting CA data from Engine...")
        # If engine CA dir doesnt exist create it and download the ca.pem
        temp_ca_file = None
        if os.path.exists(self.ca_engine):
            calculated_fprint = self._calculate_fingerprint(self.ca_engine)
        else:
            if not os.path.exists(self.ca_dir):
                os.makedirs(self.ca_dir, 0o755)
                self._silent_restorecon(self.ca_dir)

            res = self._execute_http_request(self.url_CA,
                                             cert_validation=False)

            with tempfile.NamedTemporaryFile(
                dir=os.path.dirname(self.ca_dir),
                delete=False
            ) as f:
                f.write(res)

            calculated_fprint = self._calculate_fingerprint(f.name)
            temp_ca_file = True

        if self.fprint and self.fprint.lower() != calculated_fprint.lower():
            msg = "The fingeprints doesn't match:\n" \
                  "Calculated fingerprint: [{c}]\n" \
                  "Attribute fingerprint:  [{a}]".format(c=calculated_fprint,
                                                         a=self.fprint)

            self.logger.debug(msg)
            if temp_ca_file:
                os.unlink(f.name)
            raise RuntimeError(msg)

        if temp_ca_file:
            os.rename(f.name, self.ca_engine)

        self.fprint = calculated_fprint
        self.logger.info("Calculated fingerprint: {f}".format(
                         f=self.fprint))

    def download_ssh(self):
        """
        Download ssh authorized keys and save it in the node
        """
        self.logger.info("Collecting ssh pub key data...")
        _uid = pwd.getpwnam(self.ssh_user).pw_uid
        _auth_keys_dir = pwd.getpwuid(_uid).pw_dir + "/.ssh"
        _auth_keys = _auth_keys_dir + "/authorized_keys"
        self.logger.debug("auth_key is located {f}".format(f=_auth_keys))

        if not os.path.exists(_auth_keys_dir):
            os.makedirs(_auth_keys_dir, 0o700)
            self._silent_restorecon(_auth_keys_dir)
            os.chown(_auth_keys_dir, _uid, _uid)

        res = self._execute_http_request(self.url_ssh_key)
        with tempfile.NamedTemporaryFile(
            dir=_auth_keys_dir,
            delete=False
        ) as f:
            f.write(res)

        # If ssh key is new append it into autorized_keys
        with open(f.name, "r") as f_ro:
            content = f_ro.read()
            with open(_auth_keys, "a+") as f_w:
                if content not in f_w.read():
                    f_w.write(content)
                    os.chmod(_auth_keys, 0o600)
                    self._silent_restorecon(_auth_keys)
            os.chown(_auth_keys, _uid, _uid)

        os.unlink(f.name)

    def execute_registration(self):
        """
        Trigger the registration command against Engine
        """
        self._execute_http_request(self.url_reg)
        self.logger.info("Registration completed, host is pending approval"
                         " on Engine: {e}".format(e=self.engine_fqdn))


@expose("register")
def main(*args):
    '''
    A tool which register the node against Engine
    Note: This comment is required by vdsm-tool which
          looks for a doc string.
    '''
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description='Tool to register node to Engine',
        epilog='Example of use:\n%(prog)s register '
                    '--engine-fqdn engine.mydomain'
    )

    parser.add_argument(
        '--node-address',
        help="Define node FQDN or IP address."
             " If not provided, will be used system host name",
    )

    parser.add_argument(
        '--node-name',
        help="Define node name."
             " If not provided, will be used system short host name"
             " (the name before the first dot in the system host name)",
    )

    parser.add_argument(
        '--engine-fqdn',
        help="Engine FQDN or IP address (See also: --check-fqdn)",
        required=True
    )

    parser.add_argument(
        '--engine-https-port',
        help="Define engine https port."
             " If not provided, will be used 443",
    )

    parser.add_argument(
        '--ssh-user',
        help="SSH username to establish the connection with Engine. "
             "If not provided, the user which is "
             "executing the script will catch and used",
    )

    parser.add_argument(
        '--ssh-port',
        help="SSH port to establish the connection with Engine "
             "If not provided, the script will use the default "
             "SSH port 22"
    )

    parser.add_argument(
        '--check-fqdn',
        help="Disable or Enable FQDN check for Engine CA, this option "
             "is enabled by default (Use: True or False)",
    )

    parser.add_argument(
        '--fingerprint',
        help="Specify an existing fingerprint to be validated against "
             "Engine CA fingerprint",
    )

    parser.add_argument(
        '--vdsm-port',
        help="Specify the listen port of VDSM"
             " If not provided, will be used the default 54321",
    )

    parser.add_argument(
        '--vdsm-uuid',
        help="Provide host UUID to be used instead vdsm.utils"
             " Useful for hosts with blank or buggy DMI",
    )

    # Using [1:] to remove the 'register' option from arguments
    # and avoid vdsm-tool recognize it as an unknown option
    parsed_args = parser.parse_args(args=args[1:])

    reg = Register(engine_fqdn=parsed_args.engine_fqdn,
                   engine_https_port=parsed_args.engine_https_port,
                   vdsm_port=parsed_args.vdsm_port,
                   node_address=parsed_args.node_address,
                   node_name=parsed_args.node_name,
                   ssh_user=parsed_args.ssh_user,
                   ssh_port=parsed_args.ssh_port,
                   fingerprint=parsed_args.fingerprint,
                   check_fqdn=parsed_args.check_fqdn,
                   vdsm_uuid=parsed_args.vdsm_uuid)

    try:
        reg.handshake()
        reg.host_uuid()
        reg.download_ca()
        reg.download_ssh()
        reg.execute_registration()
    except:
        reg.logger.exception(
            "Cannot connect to engine. {f} matches "
            "the FQDN of Engine?".format(f=parsed_args.engine_fqdn))
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())

"""
Registration schema:

UUID
=========
    - If there is UUID already generated for the system will be
      available in /etc/vdsm/vdsm.id

    - In case, there is no UUID, use auxiliary function from VDSM
      to generate it and store in /etc/vdsm/vdsm.id

Service reg:
============
    - REQUIRED_FOR: Engine >= 3.4

    - Process UUID

    - Download CA via get-pki-trust URL
      https://ENGINE_FQDN/ovirt-engine/services/host-register?version=1
      &command=get-pki-trust

    - Download ssh pub key via get-ssh-trust URL
      https://ENGINE_FQDN/ovirt-engine/services/host-register?version=1
      &command=get-ssh-trust

    - Register via URL:
      https://ENGINE_FQDN/ovirt-engine/services/host-register?version=1
      &command=register&name=NODE_NAME&address=NO_FQDN_OR_IP
      &uniqueId=NODE_UUID&sshUser=SSH_USERNAME&sshPort=SSHD_PORT
"""
