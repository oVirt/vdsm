#
# Copyright 2012-2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import logging
import re
import subprocess

from collections import namedtuple

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import constants
from vdsm.common.network.address import hosttail_split

log = logging.getLogger("storage.iscsiadm")

# iscsiadm exit statuses
ISCSI_ERR_SESS_EXISTS = 15
ISCSI_ERR_LOGIN_AUTH_FAILED = 24
ISCSI_ERR_OBJECT_NOT_FOUND = 21

Iface = namedtuple('Iface', 'ifacename transport_name hwaddress ipaddress \
                    net_ifacename initiatorname')


class IscsiError(RuntimeError):
    pass


class ReservedInterfaceNameError(IscsiError):
    pass


class IscsiInterfaceError(IscsiError):
    pass


class IsciInterfaceAlreadyExistsError(IscsiInterfaceError):
    pass


class IsciInterfaceCreationError(IscsiInterfaceError):
    pass


class IscsiInterfaceDoesNotExistError(IscsiInterfaceError):
    pass


class IscsiInterfaceUpdateError(IscsiInterfaceError):
    pass


class IscsiInterfaceDeletionError(IscsiInterfaceError):
    pass


class IscsiDiscoverdbError(IscsiError):
    pass


class IscsiInterfaceListingError(IscsiError):
    pass


class IscsiAuthenticationError(IscsiError):
    pass


class IscsiNodeError(IscsiError):
    pass


class IscsiSessionNotFound(IscsiError):
    pass


class IscsiSessionError(IscsiError):
    pass


class IscsiSessionRescanTimeout(IscsiSessionError):
    msg = ("Timeout scanning iSCSI sesions (pid={self.pid}, "
           "timeout={self.timeout})")

    def __init__(self, pid, timeout):
        self.pid = pid
        self.timeout = timeout

    def __str__(self):
        return self.msg.format(self=self)


_RESERVED_INTERFACES = ("default", "tcp", "iser")


def run_cmd(args):
    # FIXME: I don't use supervdsm because this entire module has to just be
    # run as root and there is no such feature yet in supervdsm. When such
    # feature exists please change this.
    cmd = [constants.EXT_ISCSIADM] + args
    out = commands.run(cmd, sudo=True)
    return out.decode("utf-8")


def iface_exists(interfaceName):
    # FIXME: can be optimized by checking /var/lib/iscsi/ifaces
    for iface in iface_list():
        if interfaceName == iface.ifacename:
            return True

    return False


def iface_new(name):
    if name in _RESERVED_INTERFACES:
        raise ReservedInterfaceNameError(name)

    try:
        run_cmd(["-m", "iface", "-I", name, "--op=new"])
    except cmdutils.Error as e:
        if iface_exists(name):
            raise IsciInterfaceAlreadyExistsError(name)

        raise IsciInterfaceCreationError(name, e.rc, e.out, e.err)


def iface_update(name, key, value):
    try:
        run_cmd(["-m", "iface", "-I", name, "-n", key, "-v", value,
                 "--op=update"])
    except cmdutils.Error as e:
        if not iface_exists(name):
            raise IscsiInterfaceDoesNotExistError(name)

        raise IscsiInterfaceUpdateError(name, e.rc, e.out, e.err)


def iface_delete(name):
    try:
        run_cmd(["-m", "iface", "-I", name, "--op=delete"])
    except cmdutils.Error:
        if not iface_exists(name):
            raise IscsiInterfaceDoesNotExistError(name)

        raise IscsiInterfaceDeletionError(name)


def iface_list(out=None):
    # FIXME: This can be done more efficiently by iterating
    # /var/lib/iscsi/ifaces. Fix if ever a performance bottleneck.
    # "iscsiadm -m iface" output format:
    #   <iscsi_ifacename> <transport_name>,<hwaddress>,<ipaddress>,\
    #   <net_ifacename>,<initiatorname>
    if out is None:
        try:
            out = run_cmd(["-m", "iface"])
        except cmdutils.Error as e:
            raise IscsiInterfaceListingError(e.rc, e.out, e.err)

    for line in out.splitlines():
        yield Iface._make(None if value == '<empty>' else value
                          for value in re.split(r'[\s,]', line))


def iface_info(name):
    # FIXME: This can be done more effciently by reading
    # /var/lib/iscsi/ifaces/<iface name>. Fix if ever a performance bottleneck.
    try:
        out = run_cmd(["-m", "iface", "-I", name])
    except cmdutils.Error as e:
        if not iface_exists(name):
            raise IscsiInterfaceDoesNotExistError(name)

        raise IscsiInterfaceListingError(e.rc, e.out, e.err)

    res = {}
    for line in out.splitlines():
        if line.startswith("#"):
            continue

        key, value = line.split("=", 1)

        if value.strip() == '<empty>':
            continue

        res[key.strip()] = value.strip()

    return res


def discoverydb_new(discoveryType, iface, portal):
    try:
        run_cmd(["-m", "discoverydb", "-t", discoveryType, "-I", iface,
                 "-p", portal, "--op=new"])
    except cmdutils.Error as e:
        if not iface_exists(iface):
            raise IscsiInterfaceDoesNotExistError(iface)

        raise IscsiDiscoverdbError(e.rc, e.out, e.err)


def discoverydb_update(discoveryType, iface, portal, key, value):
    try:
        run_cmd(["-m", "discoverydb", "-t", discoveryType, "-I", iface,
                 "-p", portal, "-n", key, "-v", value, "--op=update"])
    except cmdutils.Error as e:
        if not iface_exists(iface):
            raise IscsiInterfaceDoesNotExistError(iface)

        raise IscsiDiscoverdbError(e.rc, e.out, e.err)


def discoverydb_discover(discoveryType, iface, portal):
    try:
        out = run_cmd(
            ["-m", "discoverydb", "-t", discoveryType, "-I", iface, "-p",
             portal, "--discover"])
    except cmdutils.Error as e:
        if not iface_exists(iface):
            raise IscsiInterfaceDoesNotExistError(iface)

        if e.rc == ISCSI_ERR_LOGIN_AUTH_FAILED:
            raise IscsiAuthenticationError(e.rc, e.out, e.err)

        raise IscsiDiscoverdbError(e.rc, e.out, e.err)

    res = []
    for line in out.splitlines():
        rest, iqn = line.split()
        rest, tpgt = rest.split(",")
        ip, port = hosttail_split(rest)
        res.append((ip, int(port), int(tpgt), iqn))

    return res


def discoverydb_delete(discoveryType, iface, portal):
    try:
        run_cmd(["-m", "discoverydb", "-t", discoveryType, "-I", iface,
                 "-p", portal, "--op=delete"])
    except cmdutils.Error as e:
        if not iface_exists(iface):
            raise IscsiInterfaceDoesNotExistError(iface)

        raise IscsiDiscoverdbError(e.rc, e.out, e.err)


def node_new(iface, portal, targetName):
    try:
        run_cmd(["-m", "node", "-T", targetName, "-I", iface,
                 "-p", portal, "--op=new"])
    except cmdutils.Error as e:
        if not iface_exists(iface):
            raise IscsiInterfaceDoesNotExistError(iface)

        raise IscsiNodeError(e.rc, e.out, e.err)


def node_update(iface, portal, targetName, key, value):
    try:
        run_cmd(["-m", "node", "-T", targetName, "-I", iface,
                 "-p", portal, "-n", key, "-v", value, "--op=update"])
    except cmdutils.Error as e:
        if not iface_exists(iface):
            raise IscsiInterfaceDoesNotExistError(iface)

        raise IscsiNodeError(e.rc, e.out, e.err)


def node_delete(iface, portal, targetName):
    try:
        run_cmd(["-m", "node", "-T", targetName, "-I", iface,
                 "-p", portal, "--op=delete"])
    except cmdutils.Error as e:
        if not iface_exists(iface):
            raise IscsiInterfaceDoesNotExistError(iface)

        raise IscsiNodeError(e.rc, e.out, e.err)


def node_disconnect(iface, portal, targetName):
    try:
        run_cmd(["-m", "node", "-T", targetName, "-I", iface,
                 "-p", portal, "-u"])
    except cmdutils.Error as e:
        if not iface_exists(iface):
            raise IscsiInterfaceDoesNotExistError(iface)

        if e.rc == ISCSI_ERR_OBJECT_NOT_FOUND:
            raise IscsiSessionNotFound(iface, portal, targetName)

        raise IscsiNodeError(e.rc, e.out, e.err)


def node_login(iface, portal, targetName):
    try:
        run_cmd(["-m", "node", "-T", targetName, "-I", iface,
                 "-p", portal, "-l"])
    except cmdutils.Error as e:
        if e.rc == ISCSI_ERR_SESS_EXISTS:
            # If we have multiple portals using same (address, port, iface),
            # only one session can log in, and we get a "session exists" error
            # for the duplicate nodes.  Since we have a logged in session, we
            # can treat this as success, but we want to warn about the
            # duplicate portals.
            # https://bugzilla.redhat.com/2097614
            log.warning(
                "Duplicate portals for target %s iface %s portal %s: %s",
                targetName, iface, portal, e)
            return

        if not iface_exists(iface):
            raise IscsiInterfaceDoesNotExistError(iface)

        if e.rc == ISCSI_ERR_LOGIN_AUTH_FAILED:
            raise IscsiAuthenticationError(e.rc, e.out, e.err)

        raise IscsiNodeError(e.rc, e.out, e.err)


def session_rescan(timeout=None):
    args = [constants.EXT_ISCSIADM, "-m", "session", "-R"]

    p = commands.start(
        args,
        sudo=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # TODO: Raising a timeout allows a new scan to start before this scan
        # terminates. The new scan is likely to be blocked until this scan
        # terminates.
        commands.wait_async(p)
        raise IscsiSessionRescanTimeout(p.pid, timeout)

    # This is an expected condition before connecting to iSCSI storage
    # server during startup, or after disconnecting.
    if p.returncode == ISCSI_ERR_OBJECT_NOT_FOUND:
        log.debug("No iSCSI sessions found")
        return

    # Any other error is reported to the caller.
    if p.returncode != 0:
        raise IscsiSessionError(p.returncode, out, err)


def session_logout(sessionId):
    try:
        run_cmd(["-m", "session", "-r", str(sessionId), "-u"])
    except cmdutils.Error as e:
        raise IscsiSessionError(e.rc, e.out, e.err)
