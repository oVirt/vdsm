#
# Copyright 2009-2012 Red Hat, Inc.
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
import functools
import logging

import libvirt
from vdsm import constants, utils


class EventLoop:
    def __init__(self):
        self.run = True
        libvirt.virEventRegisterDefaultImpl()
        self.__thread = threading.Thread(target=self.__run,
                                         name="libvirtEventLoop")
        self.__thread.setDaemon(True)
        self.__thread.start()

    def __run(self):
        while self.run:
            libvirt.virEventRunDefaultImpl()

    def stop(self, wait=True):
        self.run = False
        if wait:
            self.__thread.join()

# Make sure to never reload this module, or you would lose events
__event_loop = EventLoop()


def stop_event_loop():
    global __event_loop
    __event_loop.stop()


def __eventCallback(conn, dom, *args):
    try:
        cif, eventid = args[-1]
        vmid = dom.UUIDString()
        v = cif.vmContainer.get(vmid)

        if not v:
            cif.log.debug('unknown vm %s eventid %s args %s',
                          vmid, eventid, args)
            return

        if eventid == libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE:
            event, detail = args[:-1]
            v._onLibvirtLifecycleEvent(event, detail, None)
        elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_REBOOT:
            v.onReboot()
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
        elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_WATCHDOG:
            action, = args[:-1]
            v._onWatchdogEvent(action)
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
            except libvirt.libvirtError as e:
                edom = e.get_error_domain()
                ecode = e.get_error_code()
                EDOMAINS = (libvirt.VIR_FROM_REMOTE,
                            libvirt.VIR_FROM_RPC)
                ECODES = (libvirt.VIR_ERR_SYSTEM_ERROR,
                          libvirt.VIR_ERR_INTERNAL_ERROR,
                          libvirt.VIR_ERR_NO_CONNECT,
                          libvirt.VIR_ERR_INVALID_CONN)
                if edom in EDOMAINS and ecode in ECODES:
                    cif.log.error('connection to libvirt broken. '
                                  'taking vdsm down. ecode: %d edom: %d',
                                  ecode, edom)
                    cif.prepareForShutdown()
                else:
                    cif.log.debug('Unknown libvirterror: ecode: %d edom: %d '
                                  'level: %d message: %s', ecode, edom,
                                  e.get_error_level(), e.get_error_message())
                raise
        wrapper.__name__ = f.__name__
        wrapper.__doc__ = f.__doc__
        return wrapper

    def req(credentials, user_data):
        passwd = file(constants.P_VDSM_LIBVIRT_PASSWD).readline().rstrip("\n")
        for cred in credentials:
            if cred[0] == libvirt.VIR_CRED_AUTHNAME:
                cred[4] = constants.SASL_USERNAME
            elif cred[0] == libvirt.VIR_CRED_PASSPHRASE:
                cred[4] = passwd
        return 0

    auth = [[libvirt.VIR_CRED_AUTHNAME, libvirt.VIR_CRED_PASSPHRASE],
            req, None]

    with __connectionLock:
        conn = __connections.get(id(cif))
        if not conn:
            libvirtOpenAuth = functools.partial(libvirt.openAuth,
                                                'qemu:///system', auth, 0)
            logging.debug('trying to connect libvirt')
            conn = utils.retry(libvirtOpenAuth, timeout=10, sleep=0.2)
            __connections[id(cif)] = conn
            if cif is not None:
                for ev in (libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                           libvirt.VIR_DOMAIN_EVENT_ID_REBOOT,
                           libvirt.VIR_DOMAIN_EVENT_ID_RTC_CHANGE,
                           libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR_REASON,
                           libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS,
                           libvirt.VIR_DOMAIN_EVENT_ID_BLOCK_JOB,
                           libvirt.VIR_DOMAIN_EVENT_ID_WATCHDOG):
                    conn.domainEventRegisterAny(None, ev,
                                                __eventCallback, (cif, ev))
                for name in dir(libvirt.virConnect):
                    method = getattr(conn, name)
                    if callable(method) and name[0] != '_':
                        setattr(conn, name, wrapMethod(method))
            # In case we're running into troubles with keeping the connections
            # alive we should place here:
            # conn.setKeepAlive(interval=5, count=3)
            # However the values need to be considered wisely to not affect
            # hosts which are hosting a lot of virtual machines

        return conn
