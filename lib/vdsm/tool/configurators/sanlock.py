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

import grp
import os
import re
import sys

from vdsm import constants
from vdsm import host
from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.storage import sanlockconf

from . import YES, NO

PID_FILE = "/run/sanlock/sanlock.pid"
REQUIRED_GROUPS = {constants.QEMU_PROCESS_GROUP, constants.VDSM_GROUP}

# Configuring requires stopping sanlock.
services = ('sanlock',)


def isconfigured():
    """
    Return YES if sanlock is configured, NO if sanlock need to be configured or
    restarted to pick up the currrent configuration.
    """
    groups = _groups_issues()
    if groups:
        _log("sanlock user needs groups: %s", ", ".join(groups))
        return NO

    options = _config_file_issues()
    if options:
        _log("sanlock config file needs options: %s", options)
        return NO

    if _restart_needed():
        _log("sanlock daemon needs restart")
        return NO

    _log("sanlock is configured for vdsm")
    return YES


def configure():
    """
    Configure sanlock for vdsm. The new configuration will be applied when
    sanlock is started after configuration.
    """
    if _groups_issues():
        _log("Configuring sanlock user groups")
        _configure_groups()

    if _config_file_issues():
        _log("Configuring sanlock config file")
        backup = _configure_config_file()
        if backup:
            _log("Previous sanlock.conf copied to %s", backup)


# Checking and configuring groups.


def _groups_issues():
    """
    Return set of groups missing for user sanlock.
    """
    actual_groups = {g.gr_name for g in grp.getgrall()
                     if constants.SANLOCK_USER in g.gr_mem}
    return REQUIRED_GROUPS - actual_groups


def _configure_groups():
    try:
        commands.run([
            '/usr/sbin/usermod',
            '-a',
            '-G',
            ','.join(REQUIRED_GROUPS),
            constants.SANLOCK_USER
        ])
    except cmdutils.Error as e:
        raise RuntimeError("Failed to perform sanlock config: {}".format(e))


# Checking and configuring config file.


def _config_file_issues():
    """
    Return dict of options that need to be configured for vdsm.
    """
    conf = sanlockconf.load()
    return {option: value
            for option, value in _config_for_vdsm().items()
            if conf.get(option) != value}


def _configure_config_file():
    """
    Configure sanlock.conf for vdsm. If a previous configuration exists, return
    the path to the backup file.
    """
    conf = sanlockconf.load()
    conf.update(_config_for_vdsm())
    return sanlockconf.dump(conf)


def _config_for_vdsm():
    """
    Return dict with configuration options that vdsm cares about.
    """
    hardware_id = host.uuid()
    if hardware_id is None:
        raise RuntimeError(
            "Cannot get host hardware id: please add host to engine")

    return {

        # If not configured, sanlock generates a new UUID on each
        # restart.  Using constant host name, recovery from unclean
        # shutdown is 3 times faster. Using the host hardware id will
        # make it easier to detect which host is related to sanlock
        # issues.
        # See https://bugzilla.redhat.com/1508098

        "our_host_name": hardware_id,

        # If not configured, sanlock uses 8 worker threads, limiting
        # concurrent add_lockspace calls. Using 40 worker threads,
        # activating a host with 40 storage domains is 3 times faster.
        # We use 50 worker threads to optimize for 50 storage domains.
        # See https://bugzilla.redhat.com/1902468

        "max_worker_threads": "50",
    }


# Checking daemon needs a restart.


def _restart_needed():
    """
    Return True if sanlock daemon needs a restart to pick up the current
    configuration.
    """
    groups = _daemon_groups()
    if groups is not None:
        if not REQUIRED_GROUPS.issubset(groups):
            return True

    options = _daemon_options()
    if options is not None:
        for key, value in _config_for_vdsm().items():
            # TODO: Sanlock does not report max_worker_threads yet, so we must
            # skip missing keys. Check all keys when we require sanlock version
            # reporting max_worker_threads.
            if key in options and options[key] != value:
                return True

    return False


def _daemon_groups():
    """
    If sanlock daemon is running, return the supplementary groups.
    """
    try:
        with open(PID_FILE) as f:
            sanlock_pid = f.readline().strip()
    except FileNotFoundError:
        return None

    proc_status = os.path.join('/proc', sanlock_pid, 'status')
    try:
        with open(proc_status) as f:
            status = f.read()
    except FileNotFoundError:
        return None

    match = re.search(r"^Groups:\t?(.*)$", status, re.MULTILINE)
    if not match:
        raise RuntimeError(
            "Cannot get sanlock daemon groups: {!r}".format(status))

    return {grp.getgrgid(int(s)).gr_name
            for s in match.group(1).split()}


def _daemon_options():
    """
    If sanlock daemon is running, return actual options used by the daemon.
    """
    try:
        out = commands.run(["sanlock", "client", "status", "-D"])
    except cmdutils.Error as e:
        if e.rc != 1 or e.out != b"":
            raise
        # Most likely sanlock daemon is not running.
        return None

    # Example result on misconfigured system:
    #
    # daemon 1a422c70-3b56-4677-9db8-dedb30d66824.host4
    #     our_host_name=1a422c70-3b56-4677-9db8-dedb30d66824.host4
    #     use_watchdog=1
    #     ...
    # p -1 helper
    #     ...
    #
    # See https://pagure.io/sanlock/blob/master/f/src/client_cmd.c

    lines = out.decode("utf-8").splitlines()

    if not lines[0].startswith("daemon "):
        raise RuntimeError("Unexpected response: {!r}".format(out))

    options = {}
    for line in lines[1:]:
        if not line.startswith("    "):
            break  # End of section.

        key, value = line.split("=", 1)
        options[key.lstrip()] = value

    return options


# TODO: use standard logging
def _log(fmt, *args):
    sys.stdout.write(fmt % args + "\n")
