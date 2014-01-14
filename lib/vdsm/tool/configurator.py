# Copyright 2013 Red Hat, Inc.
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

import os
import sys
import grp
import argparse

from .. import utils
from . import service, expose
from ..constants import P_VDSM_EXEC, DISKIMAGE_GROUP
from ..constants import QEMU_PROCESS_GROUP, VDSM_GROUP


class _ModuleConfigure(object):
    def __init__(self):
        pass

    def getName(self):
        return None

    def getServices(self):
        return []

    def validate(self):
        return True

    def configure(self):
        pass

    def isconfigured(self):
        return True

    def reconfigureOnForce(self):
        return True


class LibvirtModuleConfigure(_ModuleConfigure):
    def __init__(self):
        super(LibvirtModuleConfigure, self).__init__()

    def getName(self):
        return 'libvirt'

    def getServices(self):
        return ["supervdsmd", "vdsmd", "libvirtd"]

    def _exec_libvirt_configure(self, action):
        """
        Invoke libvirt_configure.sh script
        """
        if os.getuid() != 0:
            raise UserWarning("Must run as root")

        rc, out, err = utils.execCmd(
            (
                os.path.join(
                    P_VDSM_EXEC,
                    'libvirt_configure.sh'
                ),
                action,
            ),
            raw=True,
        )
        sys.stdout.write(out)
        sys.stderr.write(err)
        if rc != 0:
            raise RuntimeError("Failed to perform libvirt action.")

    def configure(self):
        self._exec_libvirt_configure("reconfigure")

    def validate(self):
        """
        Validate conflict in configured files
        """
        try:
            self._exec_libvirt_configure("test_conflict_configurations")
            return True
        except RuntimeError:
            return False

    def isconfigured(self):
        """
        Check if libvirt is already configured for vdsm
        """
        try:
            self._exec_libvirt_configure("check_if_configured")
            return True
        except RuntimeError:
            return False


class SanlockModuleConfigure(_ModuleConfigure):
    def __init__(self):
        super(SanlockModuleConfigure, self).__init__()

    def getName(self):
        return 'sanlock'

    def getServices(self):
        return ['sanlock']

    def configure(self):
        """
        Configure sanlock process groups
        """
        if os.getuid() != 0:
            raise UserWarning("Must run as root")

        rc, out, err = utils.execCmd(
            (
                '/usr/sbin/usermod',
                '-a',
                '-G',
                '%s,%s' % (QEMU_PROCESS_GROUP, VDSM_GROUP),
                'sanlock'
            ),
            raw=True,
        )
        sys.stdout.write(out)
        sys.stderr.write(err)
        if rc != 0:
            raise RuntimeError("Failed to perform sanlock config.")

    def isconfigured(self):
        """
        True if sanlock service is configured, False if sanlock service
        requires a restart to reload the relevant supplementary groups.
        """
        configured = False
        try:
            with open("/var/run/sanlock/sanlock.pid", "r") as f:
                sanlock_pid = f.readline().strip()
            with open(os.path.join('/proc', sanlock_pid, 'status'),
                      "r") as sanlock_status:
                proc_status_group_prefix = "Groups:\t"
                for status_line in sanlock_status:
                    if status_line.startswith(proc_status_group_prefix):
                        groups = [int(x) for x in
                                  status_line[len(proc_status_group_prefix):].
                                  strip().split(" ")]
                        break
                else:
                    raise RuntimeError("Unable to find sanlock service groups")
            configured = grp.getgrnam(DISKIMAGE_GROUP)[2] in groups
        except IOError as e:
            if e.errno == os.errno.ENOENT:
                sys.stdout.write("sanlock service is not running\n")
                configured = True
            else:
                raise

        if not configured:
            sys.stdout.write("sanlock service requires restart\n")
        else:
            sys.stdout.write("sanlock service is already configured\n")

        return configured

    def reconfigureOnForce(self):
        return False


__configurers = (
    LibvirtModuleConfigure(),
    SanlockModuleConfigure(),
)


@expose("configure")
def configure(*args):
    """
    Configure external services for vdsm
    """
    args = _parse_args("configure")
    configurer_to_trigger = []

    sys.stdout.write("\nChecking configuration status...\n\n")
    for c in __configurers:
        if c.getName() in args.modules:
            if not c.validate():
                raise RuntimeError(
                    "Configuration of %s is invalid" % c.getName()
                )
            if (args.force and c.reconfigureOnForce()) or not c.isconfigured():
                configurer_to_trigger.append(c)

    services = []
    for c in configurer_to_trigger:
        for s in c.getServices():
            if service.service_status(s, False) == 0:
                if not args.force:
                    raise RuntimeError(
                        "\n\nCannot configure while service '%s' is "
                        "running.\n Stop the service manually or use the "
                        "--force flag.\n" % s
                    )
                services.append(s)

    for s in services:
        service.service_stop(s)

    sys.stdout.write("\nRunning configure...\n")
    for c in configurer_to_trigger:
        c.configure()

    for s in reversed(services):
        service.service_start(s)
    sys.stdout.write("\nDone configuring modules to VDSM.\n")


@expose("is-configured")
def isconfigured(*args):
    """
    Determine if module is configured
    """
    ret = True
    args = _parse_args('is-configured')

    m = [
        c.getName() for c in __configurers
        if c.getName() in args.modules and not c.isconfigured()
    ]

    if m:
        sys.stdout.write(
            "Modules %s are not configured\n " % ','.join(m),
        )
        ret = False

    if not ret:
        msg = \
            """

One of the modules is not configured to work with VDSM.
To configure the module use the following:
'vdsm-tool configure [module_name]'.

If all modules are not configured try to use:
'vdsm-tool configure --force'
(The force flag will stop the module's service and start it
afterwards automatically to load the new configuration.)
"""
        raise RuntimeError(msg)


@expose("validate-config")
def validate_config(*args):
    """
    Determine if configuration is valid
    """
    ret = True
    args = _parse_args('validate-config')

    m = [
        c.getName() for c in __configurers
        if c.getName() in args.modules and not c.validate()
    ]

    if m:
        sys.stdout.write(
            "Modules %s contains invalid configuration\n " % ','.join(m),
        )
        ret = False

    if not ret:
        raise RuntimeError("Config is not valid. Check conf files")


def _parse_args(action):
    parser = argparse.ArgumentParser('vdsm-tool %s' % (action))
    allModules = [n.getName() for n in __configurers]
    parser.add_argument(
        '--module',
        dest='modules',
        choices=allModules,
        default=[],
        metavar='STRING',
        action='append',
        help=(
            'Specify the module to run the action on '
            '(e.g %(choices)s).\n'
            'If non is specified, operation will run for '
            'all related modules.'
        ),
    )
    if action == "configure":
        parser.add_argument(
            '--force',
            dest='force',
            default=False,
            action='store_true',
            help='Force configuration, trigger services restart',
        )
    args = parser.parse_args(sys.argv[2:])
    if not args.modules:
        args.modules = allModules
    return args
