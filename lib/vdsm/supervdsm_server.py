# Copyright 2011-2019 Red Hat, Inc.
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
import errno
import importlib
import logging
import logging.config
import os
import pkgutil
import resource
import signal
import sys
import syslog

from contextlib import closing
from functools import wraps
from multiprocessing import connection
from multiprocessing import Pipe
from multiprocessing import Process

import six

from vdsm.common import concurrent
from vdsm.common import constants
from vdsm.common import lockfile
from vdsm.common import sigutils
from vdsm.common import time
from vdsm.common import zombiereaper

try:
    from vdsm.gluster import listPublicFunctions
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False

from vdsm import supervdsm_api
from vdsm.storage import constants as sc
from vdsm.storage import fuser
from vdsm.storage import hba
from vdsm.storage import mount
from vdsm.storage.fileUtils import chown, resolveGid, resolveUid
from vdsm.storage.fileUtils import validateAccess as _validateAccess
from vdsm.storage.iscsi import getDevIscsiInfo as _getdeviSCSIinfo
from vdsm.storage.iscsi import readSessionInfo as _readSessionInfo
from vdsm.common.supervdsm import _SuperVdsmManager

from vdsm.network.initializer import init_privileged_network_components

from vdsm.config import config

_AUTHKEY = b""
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
                             func.__name__, args, kwargs)
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
    def mount(self, fs_spec, fs_file, mntOpts=None, vfstype=None,
              cgroup=None):
        mount._mount(fs_spec, fs_file, mntOpts=mntOpts, vfstype=vfstype,
                     cgroup=cgroup)

    @logDecorator
    def umount(self, fs_file, force=False, lazy=False, freeloop=False):
        mount._umount(fs_file, force=force, lazy=lazy, freeloop=freeloop)

    @logDecorator
    def getdeviSCSIinfo(self, *args, **kwargs):
        return _getdeviSCSIinfo(*args, **kwargs)

    @logDecorator
    def readSessionInfo(self, sessionID):
        return _readSessionInfo(sessionID)

    def _runAs(self, user, groups, func, args=(), kwargs={}):
        def child(pipe):
            res = ex = None
            try:
                uid = resolveUid(user)
                if groups:
                    gids = [resolveGid(g) for g in groups]

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


def __assertSingleInstance():
    try:
        lockfile.lock(os.path.join(constants.P_VDSM_RUN, 'supervdsmd.lock'))
    except Exception as e:
        raise FatalError(str(e))


def main(args):
    try:
        __assertSingleInstance()
        parser = option_parser()
        args = parser.parse_args(args=args)

        # Override user and group if called with --user and --group.
        constants.VDSM_USER = args.user
        constants.VDSM_GROUP = args.group

        # Override storage-repository, used to verify file access.
        sc.REPO_DATA_CENTER = args.data_center
        sc.REPO_MOUNT_DIR = os.path.join(args.data_center, sc.DOMAIN_MNT_POINT)

        try:
            logging.config.fileConfig(args.logger_conf,
                                      disable_existing_loggers=False)
        except Exception as e:
            raise FatalError("Cannot configure logging: %s" % e)

        log = logging.getLogger("SuperVdsm.Server")
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

        if args.enable_gluster:
            for name, func in listPublicFunctions(
                    constants.GLUSTER_MGMT_ENABLED):
                setattr(_SuperVdsm, name, bind(logDecorator(func)))

        for _, module_name, _ in pkgutil.iter_modules([supervdsm_api.
                                                       __path__[0]]):
            module = importlib.import_module('%s.%s' %
                                             (supervdsm_api.__name__,
                                              module_name))
            api_funcs = [f for _, f in six.iteritems(module.__dict__)
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
            manager = _SuperVdsmManager(address=address, authkey=_AUTHKEY)
            manager.register('instance', callable=_SuperVdsm)

            server = manager.get_server()
            server_thread = concurrent.thread(server.serve_forever)
            server_thread.start()

            chown(address, args.user, args.group)

            if args.enable_network:
                init_privileged_network_components()

            log.debug("Started serving super vdsm object")

            while _running:
                sigutils.wait_for_signal()

            log.debug("Terminated normally")
        finally:
            try:
                with connection.Client(address, authkey=_AUTHKEY) as conn:
                    server.shutdown(conn)
                server_thread.join()
            except Exception:
                # We ignore any errors here to avoid a situation where systemd
                # restarts supervdsmd just at the end of shutdown stage. We're
                # prepared to handle any mess (like existing outdated socket
                # file) in the startup stage.
                log.exception("Error while shutting down supervdsm")

    except Exception as e:
        syslog.syslog("Supervdsm failed to start: %s" % e)
        # Make it easy to debug via the shell
        raise


def option_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--sockfile',
        dest='sockfile',
        required=True,
        help="socket file path")
    parser.add_argument(
        '--pidfile',
        default=None,
        help="pid file path")
    parser.add_argument(
        '--user',
        default=constants.VDSM_USER,
        help="override user name (default %s)" % constants.VDSM_USER)
    parser.add_argument(
        '--group',
        default=constants.VDSM_GROUP,
        help="override group name (default %s)" % constants.VDSM_GROUP)
    parser.add_argument(
        '--logger-conf',
        default=LOG_CONF_PATH,
        help="logger config file path (default %s)" % LOG_CONF_PATH)
    parser.add_argument(
        '--disable-gluster',
        action='store_false',
        dest='enable_gluster',
        default=_glusterEnabled,
        help="disable gluster services (default enabled)")
    parser.add_argument(
        '--disable-network',
        action='store_false',
        dest='enable_network',
        default=True,
        help="disable network initialization (default enabled)")
    parser.add_argument(
        '--data-center',
        default=sc.REPO_DATA_CENTER,
        help=("override storage repository directory (default %s)"
              % sc.REPO_DATA_CENTER))
    return parser
