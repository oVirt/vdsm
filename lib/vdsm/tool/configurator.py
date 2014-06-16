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

import argparse
import filecmp
import grp
import os
import pwd
import rpm
import shutil
import sys
import traceback
import uuid

from configfile import ConfigFile, ParserWrapper
from ..constants import \
    LIBVIRT_SELINUX, \
    P_VDSM, \
    P_VDSM_CERT, \
    QEMU_PROCESS_GROUP, \
    SANLOCK_ENABLED, \
    SANLOCK_USER, \
    SYSCONF_PATH, \
    VDSM_GROUP
from . import NotRootError, UsageError
from . import service, expose, validate_ovirt_certs
from .. import utils
from vdsm.config import config

if utils.isOvirtNode():
    from ovirt.node.utils.fs import Config as NodeCfg


class InvalidConfig(UsageError):
    """ raise when invalid configuration passed """
    pass


class InvalidRun(UsageError):
    """ raise when the environemnt is not valid to run the command """
    pass

# Declare state of configuration
#
# CONFIGURED     = Module is set properly without any required changes on
#                  force.
# NOT_CONFIGURED = Module is not set properly for VDSM and need to be
#                  configured.
# NOT_SURE       = VDSM configured module already but on force configure vdsm
#                  will set configurations to defaults parameters.
#
CONFIGURED, NOT_CONFIGURED, NOT_SURE = range(3)


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
        return NOT_CONFIGURED

    def removeConf(self):
        pass


class LibvirtModuleConfigure(_ModuleConfigure):

    def getName(self):
        return 'libvirt'

    def _getFile(self, fname):
        return self.FILES[fname]['path']

    def getServices(self):
        return ["vdsmd", "supervdsmd", "libvirtd"]

    def configure(self):
        if os.getuid() != 0:
            raise NotRootError()

        self._sysvToUpstart()

        if utils.isOvirtNode():
            if not os.path.exists(P_VDSM_CERT):
                raise InvalidRun(
                    "vdsm: Missing certificate, vdsm not registered")
            validate_ovirt_certs.validate_ovirt_certs()

        # Remove a previous configuration (if present)
        self.removeConf()

        config.read(self._getFile('VDSM_CONF'))
        vdsmConfiguration = {
            'certs_exist': all(os.path.isfile(f) for f in [
                self.CA_FILE,
                self.CERT_FILE,
                self.KEY_FILE
            ]),
            'ssl_enabled': config.getboolean('vars', 'ssl'),
            'sanlock_enabled': SANLOCK_ENABLED,
            'libvirt_selinux': LIBVIRT_SELINUX
        }

        # write configuration
        for cfile, content in self.FILES.items():
            content['configure'](self, content, vdsmConfiguration)

    def validate(self):
        """
        Validate conflict in configured files
        """
        return self._isSslConflict()

    def isconfigured(self):
        """
        Check if libvirt is already configured for vdsm
        """
        ret = NOT_SURE
        for path in (self._getPersistedFiles()):
            if not self._openConfig(path).hasConf():
                ret = NOT_CONFIGURED

        if ret == NOT_SURE:
            sys.stdout.write("libvirt is already configured for vdsm\n")
        else:
            sys.stdout.write("libvirt is not configured for vdsm yet\n")
        return ret

    def removeConf(self):
        for cfile, content in LibvirtModuleConfigure.FILES.items():
            content['removeConf'](self, content['path'])

    def _getPersistedFiles(self):
        """
        get files where vdsm is expected to add a section.
        """
        return [
            cfile['path'] for cfile in self.FILES.values()
            if cfile['persisted']
        ]

    def _sysvToUpstart(self):
        """
        On RHEL 6, libvirtd can be started by either SysV init or Upstart.
        We prefer upstart because it respawns libvirtd if libvirtd
        crashed.
        """
        def iterateLibvirtFiles():
            ts = rpm.TransactionSet()
            for name in ['libvirt', 'libvirt-daemon']:
                for matches in ts.dbMatch('name', name):
                    for filename in matches[rpm.RPMTAG_FILENAMES]:
                        yield filename

        def reloadConfiguration():
            rc, out, err = utils.execCmd((INITCTL,
                                          "reload-configuration"))
            if rc != 0:
                sys.stdout.write(out)
                sys.stderr.write(err)
                raise InvalidRun(
                    "Failed to reload upstart configuration.")

        INITCTL = '/sbin/initctl'
        LIBVIRTD_UPSTART = 'libvirtd.upstart'
        TARGET = os.path.join(SYSCONF_PATH, "init/libvirtd.conf")

        if os.path.isfile(INITCTL) and os.access(INITCTL, os.X_OK):
            # libvirtd package does not provide libvirtd.upstart,
            # this could happen in Ubuntu or other distro,
            # so continue to use system default init mechanism
            packaged = ''
            for fname in iterateLibvirtFiles():
                if os.path.basename(fname) == LIBVIRTD_UPSTART:
                    packaged = fname
                    break

            if os.path.isfile(packaged):
                if not os.path.isfile(TARGET):
                    service.service_stop('libvirtd')
                if (not os.path.isfile(TARGET) or
                        not filecmp.cmp(packaged, TARGET)):
                    oldmod = None
                    if os.path.isfile(TARGET):
                        oldmod = os.stat(TARGET).st_mode

                    if utils.isOvirtNode():
                        NodeCfg().unpersist(TARGET)
                    shutil.copyfile(packaged, TARGET)
                    if utils.isOvirtNode():
                        NodeCfg().persist(TARGET)

                    if (oldmod is not None and
                            oldmod != os.stat(TARGET).st_mode):
                        os.chmod(TARGET, oldmod)
                    reloadConfiguration()

    def _isSslConflict(self):
        """
        return True if libvirt configuration files match ssl configuration of
        vdsm.conf.
        """
        config.read(self._getFile('VDSM_CONF'))
        ssl = config.getboolean('vars', 'ssl')

        lconf_p = ParserWrapper({
            'listen_tcp': '0',
            'auth_tcp': 'sasl',
        })
        lconf_p.read(self._getFile('LCONF'))
        listen_tcp = lconf_p.getint('listen_tcp')
        auth_tcp = lconf_p.get('auth_tcp')
        qconf_p = ParserWrapper({'spice_tls': '0'})
        qconf_p.read(self._getFile('QCONF'))
        spice_tls = qconf_p.getboolean('spice_tls')
        ret = True
        if ssl:
            if listen_tcp != 1 and auth_tcp != '"none"' and spice_tls != 0:
                sys.stdout.write(
                    "SUCCESS: ssl configured to true. No conflicts\n")
            else:
                sys.stdout.write(
                    "FAILED: "
                    "conflicting vdsm and libvirt-qemu tls configuration.\n"
                    "vdsm.conf with ssl=True "
                    "requires the following changes:\n"
                    "libvirtd.conf: listen_tcp=0, auth_tcp=\"sasl\", \n"
                    "qemu.conf: spice_tls=1.\n"
                )
                ret = False
        else:
            if listen_tcp == 1 and auth_tcp == '"none"' and spice_tls == 0:
                sys.stdout.write(
                    "SUCCESS: ssl configured to false. No conflicts.\n")
            else:
                sys.stdout.write(
                    "FAILED: "
                    "conflicting vdsm and libvirt-qemu tls configuration.\n"
                    "vdsm.conf with ssl=False "
                    "requires the following changes:\n"
                    "libvirtd.conf: listen_tcp=1, auth_tcp=\"none\", \n"
                    "qemu.conf: spice_tls=0.\n"
                )
                ret = False
        return ret

    def _isApplicable(self, fragment, vdsmConfiguration):
            """
            Return true if 'fragment' should be included for current
            configuration. An applicable fragment is a fragment who's list
            of conditions are met according to vdsmConfiguration.
            """
            applyFragment = True
            for key, booleanValue in fragment['conditions'].items():
                if vdsmConfiguration[key] != booleanValue:
                    applyFragment = False
            return applyFragment

    def _openConfig(self, path):
        return ConfigFile(path, self.CONF_VERSION)

    def _addSection(self, content, vdsmConfiguration):
        """
        Add a 'configuration section by vdsm' part to a config file.
        This section contains only keys not originally defined
        The section headers will include the current configuration version.
        """
        configuration = {}
        for fragment in content['fragments']:
            if self._isApplicable(fragment, vdsmConfiguration):
                configuration.update(fragment['content'])
        if configuration:
            with self._openConfig(content['path']) as conff:
                for key, val in configuration.items():
                    conff.addEntry(key, val)

    def _prefixAndPrepend(self, content, vdsmConfiguration):
        """
        Prefix each line with a comment and prepend a section
        from file path defined by 'content["prependFile"]'
        """
        with self._openConfig(content['path']) as conf:
            conf.prefixLines()

            with open(self._getFile(content['prependFile'])) as src_conf:
                conf.prependSection(src_conf.read())

    def _removeFile(self, content, vdsmConfiguration):
        """
        delete a file if it exists.
        """
        if utils.isOvirtNode():
            NodeCfg().delete(content['path'])
        else:
            utils.rmFile(content['path'])

    def _unprefixAndRemoveSection(self, path):
        """
        undo changes done by _prefixAndPrepend.
        """
        if os.path.exists(path):
            with self._openConfig(path) as conff:
                conff.removeConf()
                conff.unprefixLines()

    def _removeSection(self, path):
        """
        remove entire 'configuration section by vdsm' section.
        section is removed regardless of it's version.
        """
        if os.path.exists(path):
            with self._openConfig(path) as conff:
                conff.removeConf()

    # version != PACKAGE_VERSION since we do not want to update configuration
    # on every update. see 'configuration versioning:' at Configfile.py for
    # details.
    CONF_VERSION = '4.13.0'

    PKI_DIR = os.path.join(SYSCONF_PATH, 'pki/vdsm')
    CA_FILE = os.path.join(PKI_DIR, 'certs/cacert.pem')
    CERT_FILE = os.path.join(PKI_DIR, 'certs/vdsmcert.pem')
    KEY_FILE = os.path.join(PKI_DIR, 'keys/vdsmkey.pem')
    LS_CERT_DIR = os.path.join(PKI_DIR, 'libvirt-spice')

    # be sure to update CONF_VERSION accordingly when updating FILES.
    FILES = {

        'VDSM_CONF': {
            'path': os.path.join(
                SYSCONF_PATH,
                'vdsm/vdsm.conf'
            ),
            'configure': lambda x, y, z: True,
            'removeConf': lambda x, y: True,
            'persisted': False,
        },

        'LCONF': {
            'path': os.path.join(
                SYSCONF_PATH,
                'libvirt/libvirtd.conf'
            ),
            'configure': _addSection,
            'removeConf': _removeSection,
            'persisted': True,
            'fragments': [
                {
                    'conditions': {},
                    'content': {
                        'listen_addr': '"0.0.0.0"',
                        'unix_sock_group': '"' + QEMU_PROCESS_GROUP + '"',
                        'unix_sock_rw_perms': '"0770"',
                        'auth_unix_rw': '"sasl"',
                        'host_uuid': '"' + str(uuid.uuid4()) + '"',
                        'keepalive_interval': -1,
                        # FIXME until we are confident with libvirt
                        #  integration, let us have a verbose log
                        'log_outputs': (
                            '"1:file:/var/log/libvirt/libvirtd.log"'),
                        'log_filters': (
                            '"3:virobject 3:virfile 2:virnetlink '
                            '3:cgroup 3:event 3:json 1:libvirt '
                            '1:util 1:qemu"'),
                    },
                },
                {
                    'conditions': {
                        "ssl_enabled": False
                    },
                    'content': {
                        'auth_tcp': '"none"',
                        'listen_tcp': 1,
                        'listen_tls': 0,
                    },

                },
                {
                    'conditions': {
                        "ssl_enabled": True,
                        "certs_exist": True,
                    },
                    'content': {
                        'ca_file': '\"' + CA_FILE + '\"',
                        'cert_file': '\"' + CERT_FILE + '\"',
                        'key_file': '\"' + KEY_FILE + '\"',
                    },

                },
                {
                    'conditions': {
                        "ssl_enabled": True,
                        "certs_exist": False,
                    },
                    'content': {
                        'auth_tcp': '"none"',
                        'listen_tcp': 1,
                        'listen_tls': 0,
                    },

                }
            ]
        },

        'QCONF': {
            'path': os.path.join(
                SYSCONF_PATH,
                'libvirt/qemu.conf',
            ),
            'configure': _addSection,
            'removeConf': _removeSection,
            'persisted': True,
            'fragments': [
                {
                    'conditions': {},
                    'content': {
                        'dynamic_ownership': 0,
                        'save_image_format': '"lzop"',
                        'remote_display_port_min': 5900,
                        'remote_display_port_max': 6923,
                        'auto_dump_path': '"/var/log/core"',
                    },

                },
                {
                    'conditions': {
                        "ssl_enabled": False,
                    },
                    'content': {
                        'spice_tls': 0,
                    },

                },
                {
                    'conditions': {
                        "ssl_enabled": True,
                    },
                    'content': {
                        'spice_tls': 1,
                    },

                },
                {
                    'conditions': {
                        "ssl_enabled": True,
                        "certs_exist": True,
                    },
                    'content': {
                        'spice_tls_x509_cert_dir': '\"' + LS_CERT_DIR + '\"',
                    },

                },
                {
                    'conditions': {
                        "libvirt_selinux": False,
                    },
                    'content': {
                        'security_driver': '"none"',
                    },

                },

                {
                    'conditions': {
                        "sanlock_enabled": True,
                    },
                    'content': {
                        'lock_manager': '"sanlock"',
                    },

                }
            ]
        },

        'LDCONF': {
            'path': os.path.join(
                SYSCONF_PATH,
                'sysconfig/libvirtd',
            ),
            'configure': _addSection,
            'removeConf': _removeSection,
            'persisted': True,
            'fragments': [
                {
                    'conditions': {},
                    'content': {
                        'LIBVIRTD_ARGS': '--listen',
                        'DAEMON_COREFILE_LIMIT': 'unlimited',
                    },

                }]
        },

        'QLCONF': {
            'path': os.path.join(
                SYSCONF_PATH,
                'libvirt/qemu-sanlock.conf',
            ),
            'configure': _addSection,
            'removeConf': _removeSection,
            'persisted': True,
            'fragments': [
                {
                    'conditions': {
                        "sanlock_enabled": True,
                    },
                    'content': {
                        'auto_disk_leases': 0,
                        'require_lease_for_disks': 0,
                    },

                },
                {
                    'conditions': {},
                    'content': {
                        'auto_disk_leases': 0,
                        'require_lease_for_disks': 0,
                    },

                }
            ]
        },

        'LRCONF': {
            'path': os.path.join(
                SYSCONF_PATH,
                'logrotate.d/libvirtd',
            ),
            'configure': _prefixAndPrepend,
            'prependFile': 'LRCONF_EXAMPLE',
            'removeConf': _unprefixAndRemoveSection,
            'persisted': True,
        },

        'LRCONF_EXAMPLE': {
            'path': os.path.join(
                P_VDSM,
                'tool',
                'libvirtd.logrotate',
            ),
            'configure': lambda x, y, z: True,
            'removeConf': lambda x, y: True,
            'persisted': False,
        },


        'QNETWORK': {
            'path': os.path.join(
                SYSCONF_PATH,
                'libvirt/qemu/networks/autostart/default.xml',
            ),
            'configure': _removeFile,
            'removeConf': lambda x, y: True,
            'persisted': False,
        }
    }


class SanlockModuleConfigure(_ModuleConfigure):

    SANLOCK_GROUPS = (QEMU_PROCESS_GROUP, VDSM_GROUP)

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
            raise NotRootError()

        rc, out, err = utils.execCmd(
            (
                '/usr/sbin/usermod',
                '-a',
                '-G',
                ','.join(self.SANLOCK_GROUPS),
                SANLOCK_USER
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
        configured = NOT_CONFIGURED
        groups = [g.gr_name for g in grp.getgrall()
                  if SANLOCK_USER in g.gr_mem]
        gid = pwd.getpwnam(SANLOCK_USER).pw_gid
        groups.append(grp.getgrgid(gid).gr_name)
        if all(group in groups for group in self.SANLOCK_GROUPS):
            configured = NOT_SURE

        if configured == NOT_SURE:
            try:
                with open("/var/run/sanlock/sanlock.pid", "r") as f:
                    sanlock_pid = f.readline().strip()
                with open(os.path.join('/proc', sanlock_pid, 'status'),
                          "r") as sanlock_status:
                    proc_status_group_prefix = "Groups:\t"
                    for status_line in sanlock_status:
                        if status_line.startswith(proc_status_group_prefix):
                            groups = [int(x) for x in status_line[
                                len(proc_status_group_prefix):]
                                .strip().split(" ")]
                            break
                    else:
                        raise InvalidConfig(
                            "Unable to find sanlock service groups"
                        )

                is_sanlock_groups_set = True
                for g in self.SANLOCK_GROUPS:
                    if grp.getgrnam(g)[2] not in groups:
                        is_sanlock_groups_set = False
                if is_sanlock_groups_set:
                    configured = CONFIGURED

            except IOError as e:
                if e.errno == os.errno.ENOENT:
                    configured = CONFIGURED
                else:
                    raise

        return configured


__configurers = (
    LibvirtModuleConfigure(),
    SanlockModuleConfigure(),
)


@expose("configure")
def configure(*args):
    """
    configure [-h|...]
    Configure external services for vdsm
    Invoke with -h for complete usage.
    """
    args = _parse_args(*args)
    configurer_to_trigger = []

    sys.stdout.write("\nChecking configuration status...\n\n")
    for c in __configurers:
        if c.getName() in args.modules:
            override = args.force and (c.isconfigured != CONFIGURED)
            if not override and not c.validate():
                raise InvalidConfig(
                    "Configuration of %s is invalid" % c.getName()
                )
            if override:
                configurer_to_trigger.append(c)

    services = []
    for c in configurer_to_trigger:
        for s in c.getServices():
            if service.service_status(s, False) == 0:
                if not args.force:
                    raise InvalidRun(
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
        sys.stdout.write("Reconfiguration of %s is done.\n" % (c.getName(),))

    for s in reversed(services):
        service.service_start(s)
    sys.stdout.write("\nDone configuring modules to VDSM.\n")


@expose("is-configured")
def isconfigured(*args):
    """
    is-configured [-h|...]
    Determine if module is configured
    Invoke with -h for complete usage.
    """
    ret = True
    args = _parse_args(*args)

    m = [
        c.getName() for c in __configurers
        if c.getName() in args.modules and c.isconfigured() == NOT_CONFIGURED
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
        raise InvalidRun(msg)


@expose("validate-config")
def validate_config(*args):
    """
    validate-config [-h|...]
    Determine if configuration is valid
    Invoke with -h for complete usage.
    """
    ret = True
    args = _parse_args(*args)

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
        raise InvalidConfig("Config is not valid. Check conf files")


@expose("remove-config")
def remove_config(*args):
    """
    Remove vdsm configuration from conf files
    """
    args = _parse_args(*args)
    failed = False
    for c in __configurers:
        if c.getName() in args.modules:
            try:
                c.removeConf()
                sys.stderr.write(
                    "removed configuration of module %s successfully\n" %
                    c.getName()
                )

            except Exception:
                sys.stderr.write(
                    "can't remove configuration of module %s\n" %
                    c.getName()
                )
                traceback.print_exc(file=sys.stderr)
                failed = True
    if failed:
        raise InvalidRun("Remove configuration failed")


def _parse_args(action, *args):
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
    args = parser.parse_args(args)
    if not args.modules:
        args.modules = allModules
    return args
