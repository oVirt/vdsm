#
# Copyright 2009-2021 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

from __future__ import absolute_import
from __future__ import print_function
import sys

# When using Python 2, we must monkey patch threading module before importing
# any other module.
if sys.version_info[0] == 2:
    import pthreading  # pylint: disable=import-error
    pthreading.monkey_patch()

import atexit
import os
import os.path
import signal
import getpass
import pwd
import grp
import threading
import logging
import syslog
import resource
import tempfile
from logging import config as lconfig

from vdsm import constants
from vdsm import health
from vdsm import jobs
from vdsm import schedule
from vdsm import taskset
from vdsm import metrics
from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import dsaversion
from vdsm.common import hooks
from vdsm.common import lockfile
from vdsm.common import libvirtconnection
from vdsm.common import sigutils
from vdsm.common import time
from vdsm.common import zombiereaper
from vdsm.common.panic import panic
from vdsm.config import config
from vdsm.network.initializer import init_unprivileged_network_components
from vdsm.network.initializer import stop_unprivileged_network_components
from vdsm.profiling import profile
from vdsm.storage.hsm import HSM
from vdsm.storage.dispatcher import Dispatcher
from vdsm.virt import periodic


loggerConfFile = constants.P_VDSM_CONF + 'logger.conf'


class FatalError(Exception):
    """ Raised when vdsm fail to start """


def serve_clients(log):
    cif = None
    irs = None
    scheduler = None
    running = [True]

    def sigtermHandler(signum, frame):
        log.info("Received signal %s, shutting down" % signum)
        running[0] = False

    def sigusr1Handler(signum, frame):
        if irs:
            log.info("Received signal %s, stopping SPM" % signum)
            # pylint: disable=no-member
            # TODO remove when side effect removed from HSM.__init__ and
            # initialize it in line #63
            irs.spmStop(
                irs.getConnectedStoragePoolsList()['poollist'][0])

    def sigalrmHandler(signum, frame):
        # Used in panic.panic() when shuting down logging, must not log.
        raise RuntimeError("Alarm timeout")

    sigutils.register()
    signal.signal(signal.SIGTERM, sigtermHandler)
    signal.signal(signal.SIGUSR1, sigusr1Handler)
    signal.signal(signal.SIGALRM, sigalrmHandler)
    zombiereaper.registerSignalHandler()

    profile.start()
    metrics.start()

    libvirtconnection.start_event_loop()

    try:
        if config.getboolean('irs', 'irs_enable'):
            try:
                irs = Dispatcher(HSM())
            except:
                panic("Error initializing IRS")

        scheduler = schedule.Scheduler(name="vdsm.Scheduler",
                                       clock=time.monotonic_time)
        scheduler.start()

        from vdsm.clientIF import clientIF  # must import after config is read
        cif = clientIF.getInstance(irs, log, scheduler)

        jobs.start(scheduler, cif)

        install_manhole({'irs': irs, 'cif': cif})

        cif.start()

        init_unprivileged_network_components(cif)

        periodic.start(cif, scheduler)
        health.start()
        try:
            while running[0]:
                sigutils.wait_for_signal()

            profile.stop()
            if config.getboolean('devel', 'coverage_enable'):
                atexit._run_exitfuncs()
        finally:
            stop_unprivileged_network_components()
            metrics.stop()
            health.stop()
            periodic.stop()
            cif.prepareForShutdown()
            jobs.stop()
            scheduler.stop()
            run_stop_hook()
    finally:
        libvirtconnection.stop_event_loop(wait=False)


def run():
    try:
        lconfig.fileConfig(loggerConfFile, disable_existing_loggers=False)
    except Exception as e:
        raise FatalError("Cannot configure logging: %s" % e)

    # Shorten WARNING and CRITICAL to make the log align nicer.
    logging.addLevelName(logging.WARNING, 'WARN')
    logging.addLevelName(logging.CRITICAL, 'CRIT')

    log = logging.getLogger('vds')
    try:
        logging.root.handlers.append(logging.StreamHandler())
        log.handlers.append(logging.StreamHandler())

        sysname, nodename, release, version, machine = os.uname()
        log.info('(PID: %s) I am the actual vdsm %s %s (%s)',
                 os.getpid(), dsaversion.raw_version_revision, nodename,
                 release)

        try:
            __set_cpu_affinity()
        except Exception:
            log.exception('Failed to set affinity, running without')

        serve_clients(log)
    except:
        log.error("Exception raised", exc_info=True)

    log.info("Stopping threads")
    for t in threading.enumerate():
        if hasattr(t, 'stop'):
            log.info("Stopping %s", t)
            t.stop()

    me = threading.current_thread()
    for t in threading.enumerate():
        if t is not me:
            log.debug("%s is still running", t)

    log.info("Exiting")


def install_manhole(locals):
    if not config.getboolean('devel', 'manhole_enable'):
        return

    import manhole  # pylint: disable=import-error

    # locals:             Set the locals in the manhole shell
    # socket_path:        Set to create secure and easy to use manhole socket,
    #                     instead of /tmp/manhole-<vdsm-pid>.
    # daemon_connection:  Enable to ensure that manhole connection thread will
    #                     not block shutdown.
    # patch_fork:         Disable to avoid creation of a manhole thread in the
    #                     child process after fork.
    # sigmask:            Disable to avoid pointless modification of the
    #                     process signal mask if signlfd module is available.
    # redirect_stderr:    Disable since Python prints ignored exepctions to
    #                     stderr.

    path = os.path.join(constants.P_VDSM_RUN, 'vdsmd.manhole')
    manhole.install(locals=locals, socket_path=path, daemon_connection=True,
                    patch_fork=False, sigmask=None, redirect_stderr=False)


def __assertLogPermission():
    if not os.access(constants.P_VDSM_LOG, os.W_OK):
        raise FatalError("Cannot access vdsm log dirctory")

    logfile = constants.P_VDSM_LOG + "/vdsm.log"
    if not os.path.exists(logfile):
        # if file not exist, and vdsm has an access to log directory- continue
        return

    if not os.access(logfile, os.W_OK):
        raise FatalError("Cannot access vdsm log file")


def __assertSingleInstance():
    try:
        lockfile.lock(os.path.join(constants.P_VDSM_RUN, 'vdsmd.lock'))
    except Exception as e:
        raise FatalError(str(e))


def __assertVdsmUser():
    username = getpass.getuser()
    if username != constants.VDSM_USER:
        raise FatalError("Not running as %r, trying to run as %r"
                         % (constants.VDSM_USER, username))
    group = grp.getgrnam(constants.VDSM_GROUP)
    if (constants.VDSM_USER not in group.gr_mem) and \
       (pwd.getpwnam(constants.VDSM_USER).pw_gid != group.gr_gid):
        raise FatalError("Vdsm user is not in KVM group")


def __assertVdsmHome():
    home = os.path.expanduser("~")
    if not os.access(home, os.F_OK | os.R_OK | os.W_OK | os.X_OK):
        raise FatalError("Home directory: '%s' doesn't exist or doesn't "
                         "have correct permissions" % home)


def __assertSudoerPermissions():
    with tempfile.NamedTemporaryFile() as dst:
        # This cmd choice is arbitrary to validate that sudoers.d/50_vdsm file
        # is read properly
        cmd = [constants.EXT_CHOWN, "%s:%s" %
               (constants.VDSM_USER, constants.QEMU_PROCESS_GROUP), dst.name]
        try:
            commands.run(cmd, sudo=True)
        except cmdutils.Error as e:
            msg = ("Vdsm user could not manage to run sudo operation: "
                   "(stderr: %s). Verify sudoer rules configuration" % e.err)
            raise FatalError(msg)


def __set_cpu_affinity():
    cpu_affinity = config.get('vars', 'cpu_affinity')
    if cpu_affinity == "":
        return

    online_cpus = taskset.online_cpus()

    log = logging.getLogger('vds')

    if len(online_cpus) == 1:
        log.debug('Only one cpu detected: affinity disabled')
        return

    if cpu_affinity.lower() == taskset.AUTOMATIC:
        cpu_set = frozenset((taskset.pick_cpu(online_cpus),))
    else:
        cpu_set = frozenset(int(cpu.strip())
                            for cpu in cpu_affinity.split(","))

    log.info('VDSM will run with cpu affinity: %s', cpu_set)
    taskset.set(os.getpid(), cpu_set, all_tasks=True)


def run_stop_hook():
    # TODO: Move to vdsmd.service ExecStopPost when systemd is fixed.
    # https://bugzilla.redhat.com/1761260
    log = logging.getLogger('vds')
    try:
        hooks.after_vdsm_stop()
    except Exception:
        log.exception("Error running stop hook")


def main():
    try:
        __assertSingleInstance()
        __assertVdsmUser()
        __assertVdsmHome()
        __assertLogPermission()
        __assertSudoerPermissions()

        if not config.getboolean('vars', 'core_dump_enable'):
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        run()
    except FatalError as e:
        syslog.syslog("VDSM failed to start: %s" % e)
        # Make it easy to debug via the shell
        raise
