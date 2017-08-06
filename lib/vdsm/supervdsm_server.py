# Copyright 2011-2017 Red Hat, Inc.
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

import argparse
from pwd import getpwnam
import sys
import os
import errno
import importlib
import pkgutil
from functools import wraps
import resource
import signal
import syslog
import logging
import logging.config
from contextlib import closing

from vdsm.common import concurrent
from vdsm.common import fileutils
from vdsm.common import sigutils
from vdsm.common import time
from vdsm.common import zombiereaper

from multiprocessing import Pipe, Process
try:
    from vdsm.gluster import listPublicFunctions
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False

from vdsm import supervdsm_api
from vdsm.storage import fuser
from vdsm.storage import hba
from vdsm.storage import mount
from vdsm.storage.devicemapper import _removeMapping, _getPathsStatus
from vdsm.storage.fileUtils import chown, resolveGid, resolveUid
from vdsm.storage.fileUtils import validateAccess as _validateAccess
from vdsm.storage.iscsi import getDevIscsiInfo as _getdeviSCSIinfo
from vdsm.storage.iscsi import readSessionInfo as _readSessionInfo
from vdsm.supervdsm import _SuperVdsmManager

from vdsm.network.initializer import init_privileged_network_components

from vdsm.storage.multipath import getScsiSerial as _getScsiSerial
from vdsm.storage import multipath
from vdsm.constants import METADATA_GROUP, \
    VDSM_USER, GLUSTER_MGMT_ENABLED
from vdsm.config import config

RUN_AS_TIMEOUT = config.getint("irs", "process_pool_timeout")

_running = True


class FatalError(Exception):
    """ Raised when supervdsm fails to start """


class Timeout(RuntimeError):
    pass


LOG_CONF_PATH = "/etc/vdsm/svdsm.logger.conf"


def logDecorator(func):
    callbackLogger = logging.getLogger("SuperVdsm.ServerCallback")

    @wraps(func)
    def wrapper(*args, **kwargs):
        callbackLogger.debug('call %s with %s %s',
                             func.__name__, args[1:], kwargs)
        try:
            res = func(*args, **kwargs)
        except:
            callbackLogger.error("Error in %s", func.__name__, exc_info=True)
            raise
        callbackLogger.debug('return %s with %s',
                             func.__name__, res)
        return res
    return wrapper


def safe_poll(mp_connection, timeout):
    """
    This is a workaround until we get the PEP-475 fix for EINTR.  It
    ensures that a multiprocessing.connection.poll() will not return
    before the timeout due to an interruption.

    Returns True if there is any data to read from the pipe or if the
    pipe was closed.  Returns False if the timeout expired.
    """
    deadline = time.monotonic_time() + timeout
    remaining = timeout

    while not mp_connection.poll(remaining):
        remaining = deadline - time.monotonic_time()
        if remaining <= 0:
            return False

    return True


class _SuperVdsm(object):

    log = logging.getLogger("SuperVdsm.ServerCallback")

    @logDecorator
    def getScsiSerial(self, *args, **kwargs):
        return _getScsiSerial(*args, **kwargs)

    @logDecorator
    def mount(self, fs_spec, fs_file, mntOpts=None, vfstype=None, timeout=None,
              cgroup=None):
        mount._mount(fs_spec, fs_file, mntOpts=mntOpts, vfstype=vfstype,
                     timeout=timeout, cgroup=cgroup)

    @logDecorator
    def umount(self, fs_file, force=False, lazy=False, freeloop=False,
               timeout=None):
        mount._umount(fs_file, force=force, lazy=lazy, freeloop=freeloop,
                      timeout=timeout)

    @logDecorator
    def resizeMap(self, devName):
        return multipath._resize_map(devName)

    @logDecorator
    def removeDeviceMapping(self, devName):
        return _removeMapping(devName)

    @logDecorator
    def getdeviSCSIinfo(self, *args, **kwargs):
        return _getdeviSCSIinfo(*args, **kwargs)

    @logDecorator
    def readSessionInfo(self, sessionID):
        return _readSessionInfo(sessionID)

    @logDecorator
    def getPathsStatus(self):
        return _getPathsStatus()

    def _runAs(self, user, groups, func, args=(), kwargs={}):
        def child(pipe):
            res = ex = None
            try:
                uid = resolveUid(user)
                if groups:
                    gids = map(resolveGid, groups)

                    os.setgid(gids[0])
                    os.setgroups(gids)
                os.setuid(uid)

                res = func(*args, **kwargs)
            except BaseException as e:
                ex = e

            pipe.send((res, ex))
            pipe.recv()

        pipe, hisPipe = Pipe()
        with closing(pipe), closing(hisPipe):
            proc = Process(target=child, args=(hisPipe,))
            proc.start()

            needReaping = True
            try:
                if not safe_poll(pipe, RUN_AS_TIMEOUT):
                    try:

                        os.kill(proc.pid, signal.SIGKILL)
                    except OSError as e:
                        # Don't add to zombiereaper of PID no longer exists
                        if e.errno == errno.ESRCH:
                            needReaping = False
                        else:
                            raise

                    raise Timeout()

                res, err = pipe.recv()
                pipe.send("Bye")
                proc.terminate()

                if err is not None:
                    raise err

                return res

            finally:
                # Add to zombiereaper if process has not been waited upon
                if proc.exitcode is None and needReaping:
                    zombiereaper.autoReapPID(proc.pid)

    @logDecorator
    def validateAccess(self, user, groups, *args, **kwargs):
        return self._runAs(user, groups, _validateAccess, args=args,
                           kwargs=kwargs)

    @logDecorator
    def fuser(self, *args, **kwargs):
        return fuser.fuser(*args, **kwargs)

    @logDecorator
    def hbaRescan(self):
        return hba._rescan()


def terminate(signo, frame):
    global _running
    _running = False


def main(args):
    try:
        try:
            logging.config.fileConfig(LOG_CONF_PATH,
                                      disable_existing_loggers=False)
        except Exception as e:
            raise FatalError("Cannot configure logging: %s" % e)

        log = logging.getLogger("SuperVdsm.Server")
        parser = option_parser()
        args = parser.parse_args(args=args)
        sockfile = args.sockfile
        pidfile = args.pidfile
        if not config.getboolean('vars', 'core_dump_enable'):
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        sigutils.register()
        zombiereaper.registerSignalHandler()

        def bind(func):
            def wrapper(_SuperVdsm, *args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        if _glusterEnabled:
            for name, func in listPublicFunctions(GLUSTER_MGMT_ENABLED):
                setattr(_SuperVdsm, name, bind(logDecorator(func)))

        for _, module_name, _ in pkgutil.iter_modules([supervdsm_api.
                                                       __path__[0]]):
            module = importlib.import_module('%s.%s' %
                                             (supervdsm_api.__name__,
                                              module_name))
            api_funcs = [f for _, f in module.__dict__.iteritems()
                         if callable(f) and getattr(f, 'exposed_api', False)]
            for func in api_funcs:
                setattr(_SuperVdsm, func.__name__, bind(logDecorator(func)))

        log.debug("Making sure I'm root - SuperVdsm")
        if os.geteuid() != 0:
            sys.exit(errno.EPERM)

        if pidfile:
            pid = str(os.getpid())
            with open(pidfile, 'w') as f:
                f.write(pid + "\n")

        log.debug("Parsing cmd args")
        address = sockfile

        log.debug("Cleaning old socket %s", address)
        if os.path.exists(address):
            os.unlink(address)

        log.debug("Setting up keep alive thread")

        try:
            signal.signal(signal.SIGTERM, terminate)
            signal.signal(signal.SIGINT, terminate)

            log.debug("Creating remote object manager")
            manager = _SuperVdsmManager(address=address, authkey='')
            manager.register('instance', callable=_SuperVdsm)

            server = manager.get_server()
            servThread = concurrent.thread(server.serve_forever)
            servThread.start()

            chown(address, getpwnam(VDSM_USER).pw_uid, METADATA_GROUP)

            log.debug("Started serving super vdsm object")

            init_privileged_network_components()

            while _running:
                sigutils.wait_for_signal()

            log.debug("Terminated normally")
        finally:
            if os.path.exists(address):
                fileutils.rm_file(address)

    except Exception as e:
        syslog.syslog("Supervdsm failed to start: %s" % e)
        # Make it easy to debug via the shell
        raise


def option_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sockfile', dest='sockfile', required=True,
                        help="socket file path")
    parser.add_argument('--pidfile', dest='pidfile', default=None,
                        help="pid file path")
    return parser
