#
# Copyright 2009-2013 Red Hat, Inc.
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
import os
import signal

import libvirt
from . import constants, utils

log = logging.getLogger("libvirtconnection")


class _EventLoop:
    def __init__(self):
        self.run = False
        self.__thread = None

    def start(self):
        assert not self.run
        self.__thread = threading.Thread(target=self.__run,
                                         name="libvirtEventLoop")
        self.__thread.setDaemon(True)
        self.__thread.start()
        self.run = True

    def stop(self, wait=True):
        if self.run:
            self.run = False
            if wait:
                self.__thread.join()
            self.__thread = None

    @utils.traceback(on=log.name)
    def __run(self):
        libvirt.virEventRegisterDefaultImpl()
        while self.run:
            libvirt.virEventRunDefaultImpl()


# Make sure to never reload this module, or you would lose events
__event_loop = _EventLoop()


def start_event_loop():
    __event_loop.start()


def stop_event_loop():
    __event_loop.stop()


__connections = {}
__connectionLock = threading.Lock()


def get(target=None, killOnFailure=True):
    """Return current connection to libvirt or open a new one.
    Use target to get/create the connection object linked to that object.
    target must have a callable attribute named 'dispatchLibvirtEvents' which
    will be registered as a callback on libvirt events.

    Wrap methods of connection object so that they catch disconnection, and
    take the current process down.
    """
    def wrapMethod(f):
        @functools.wraps(f)
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
                    try:
                        __connections.get(id(target)).pingLibvirt()
                    except libvirt.libvirtError as e:
                        edom = e.get_error_domain()
                        ecode = e.get_error_code()
                        if edom in EDOMAINS and ecode in ECODES:
                            log.warning('connection to libvirt broken.'
                                        ' ecode: %d edom: %d', ecode, edom)
                            if killOnFailure:
                                log.critical('taking calling process down.')
                                os.kill(os.getpid(), signal.SIGTERM)
                            else:
                                raise
                log.debug('Unknown libvirterror: ecode: %d edom: %d '
                          'level: %d message: %s', ecode, edom,
                          e.get_error_level(), e.get_error_message())
                raise
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
        conn = __connections.get(id(target))
        if not conn:
            libvirtOpenAuth = functools.partial(libvirt.openAuth,
                                                'qemu:///system', auth, 0)
            log.debug('trying to connect libvirt')
            conn = utils.retry(libvirtOpenAuth, timeout=10, sleep=0.2)
            __connections[id(target)] = conn

            setattr(conn, 'pingLibvirt', getattr(conn, 'getLibVersion'))
            for name in dir(libvirt.virConnect):
                method = getattr(conn, name)
                if callable(method) and name[0] != '_':
                    setattr(conn, name, wrapMethod(method))
            if target is not None:
                for ev in (libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                           libvirt.VIR_DOMAIN_EVENT_ID_REBOOT,
                           libvirt.VIR_DOMAIN_EVENT_ID_RTC_CHANGE,
                           libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR_REASON,
                           libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS,
                           libvirt.VIR_DOMAIN_EVENT_ID_BLOCK_JOB,
                           libvirt.VIR_DOMAIN_EVENT_ID_WATCHDOG):
                    conn.domainEventRegisterAny(None,
                                                ev,
                                                target.dispatchLibvirtEvents,
                                                ev)
            # In case we're running into troubles with keeping the connections
            # alive we should place here:
            # conn.setKeepAlive(interval=5, count=3)
            # However the values need to be considered wisely to not affect
            # hosts which are hosting a lot of virtual machines

        return conn
