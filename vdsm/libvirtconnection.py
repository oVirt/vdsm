#
# Copyright 2009-2011 Red Hat, Inc.
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

import threading

import libvirt

import libvirtev
from vdsm import constants

# Make sure to never reload this module, or you would lose events
# TODO: make this internal to libvirtev, and make the thread stoppable
libvirtev.virEventLoopPureStart()

def __eventCallback(conn, dom, *args):
    try:
        cif, eventid = args[-1]
        vmid = dom.UUIDString()
        v = cif.vmContainer.get(vmid)

        if not v:
            cif.log.debug('unknown vm %s eventid %s args %s', vmid, eventid,
                    args)
            return

        if eventid == libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE:
            event, detail = args[:-1]
            v._onLibvirtLifecycleEvent(event, detail, None)
        elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_REBOOT:
            v.onReboot(False)
        elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_RTC_CHANGE:
            utcoffset, = args[:-1]
            v._rtcUpdate(utcoffset)
        elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR_REASON:
            srcPath, devAlias, action, reason = args[:-1]
            v._onAbnormalStop(devAlias, reason)
        elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS:
            phase, localAddr, remoteAddr, authScheme, subject = args[:-1]
            v.log.debug('graphics event phase %s localAddr %s remoteAddr %s'
                        'authScheme %s subject %s',
                        phase, localAddr, remoteAddr, authScheme, subject)
            if phase == libvirt.VIR_DOMAIN_EVENT_GRAPHICS_INITIALIZE:
                v.onConnect(remoteAddr['node'])
            elif phase == libvirt.VIR_DOMAIN_EVENT_GRAPHICS_DISCONNECT:
                v.onDisconnect()
        elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_BLOCK_JOB:
            path, type, status = args[:-1]
            v._onBlockJobEvent(path, type, status)
        else:
            v.log.warning('unknown eventid %s args %s', eventid, args)
    except:
        cif.log.error("Error running VM callback", exc_info=True)

__connections = {}
__connectionLock = threading.Lock()
def get(cif=None):
    """Return current connection to libvirt or open a new one.

    Wrap methods of connection object so that they catch disconnection, and
    take vdsm down.
    """
    def wrapMethod(f):
        def wrapper(*args, **kwargs):
            try:
                ret = f(*args, **kwargs)
                if isinstance(ret, libvirt.virDomain):
                    for name in dir(ret):
                        method = getattr(ret, name)
                        if callable(method) and name[0] != '_':
                            setattr(ret, name, wrapMethod(method))
                return ret
            except libvirt.libvirtError, e:
                if (e.get_error_domain() in (libvirt.VIR_FROM_REMOTE, libvirt.VIR_FROM_RPC)
                    and e.get_error_code() == libvirt.VIR_ERR_SYSTEM_ERROR):
                    cif.log.error('connection to libvirt broken. '
                                  'taking vdsm down.')
                    cif.prepareForShutdown()
                raise
        wrapper.__name__ = f.__name__
        wrapper.__doc__ = f.__doc__
        return wrapper

    def req(credentials, user_data):
        for cred in credentials:
            if cred[0] == libvirt.VIR_CRED_AUTHNAME:
                cred[4] = constants.SASL_USERNAME
            elif cred[0] == libvirt.VIR_CRED_PASSPHRASE:
                cred[4] = file(constants.P_VDSM_LIBVIRT_PASSWD).readline().rstrip("\n")
        return 0

    auth = [[libvirt.VIR_CRED_AUTHNAME, libvirt.VIR_CRED_PASSPHRASE], req, None]

    with __connectionLock:
        conn = __connections.get(id(cif))
        if not conn:
            conn = libvirt.openAuth('qemu:///system', auth, 0)
            __connections[id(cif)] = conn
            if cif != None:
                for ev in (libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                           libvirt.VIR_DOMAIN_EVENT_ID_REBOOT,
                           libvirt.VIR_DOMAIN_EVENT_ID_RTC_CHANGE,
                           libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR_REASON,
                           libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS,
                           libvirt.VIR_DOMAIN_EVENT_ID_BLOCK_JOB):
                    conn.domainEventRegisterAny(None, ev,
                                                __eventCallback, (cif, ev))
                for name in dir(libvirt.virConnect):
                    method = getattr(conn, name)
                    if callable(method) and name[0] != '_':
                        setattr(conn, name, wrapMethod(method))

        return conn
