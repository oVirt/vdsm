# Copyright 2014 Red Hat, Inc.
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
import shutil
import sys
import tempfile
import time

from .import \
    ModuleConfigure, \
    YES, \
    NO
from .. import service
from ... import utils
from ... import constants


class Configurator(ModuleConfigure):

    _MPATH_CONF = "/etc/multipath.conf"

    _STRG_MPATH_CONF = (
        "\n\n"
        "defaults {\n"
        "    polling_interval        5\n"
        "    getuid_callout          \"%(scsi_id_path)s --whitelisted "
        "--replace-whitespace --device=/dev/%%n\"\n"
        "    no_path_retry           fail\n"
        "    user_friendly_names     no\n"
        "    flush_on_last_del       yes\n"
        "    fast_io_fail_tmo        5\n"
        "    dev_loss_tmo            30\n"
        "    max_fds                 4096\n"
        "}\n"
        "\n"
        "devices {\n"
        "device {\n"
        "    vendor                  \"HITACHI\"\n"
        "    product                 \"DF.*\"\n"
        "    getuid_callout          \"%(scsi_id_path)s --whitelisted "
        "--replace-whitespace --device=/dev/%%n\"\n"
        "}\n"
        "device {\n"
        "    vendor                  \"COMPELNT\"\n"
        "    product                 \"Compellent Vol\"\n"
        "    no_path_retry           fail\n"
        "}\n"
        "device {\n"
        "    # multipath.conf.default\n"
        "    vendor                  \"DGC\"\n"
        "    product                 \".*\"\n"
        "    product_blacklist       \"LUNZ\"\n"
        "    path_grouping_policy    \"group_by_prio\"\n"
        "    path_checker            \"emc_clariion\"\n"
        "    hardware_handler        \"1 emc\"\n"
        "    prio                    \"emc\"\n"
        "    failback                immediate\n"
        "    rr_weight               \"uniform\"\n"
        "    # vdsm required configuration\n"
        "    getuid_callout          \"%(scsi_id_path)s --whitelisted "
        "--replace-whitespace --device=/dev/%%n\"\n"
        "    features                \"0\"\n"
        "    no_path_retry           fail\n"
        "}\n"
        "}"
    )

    _MAX_CONF_COPIES = 5

    # conf file configured by vdsm should contain a tag
    # in form of "RHEV REVISION X.Y"
    _OLD_TAGS = ["# RHAT REVISION 0.2", "# RHEV REVISION 0.3",
                 "# RHEV REVISION 0.4", "# RHEV REVISION 0.5",
                 "# RHEV REVISION 0.6", "# RHEV REVISION 0.7",
                 "# RHEV REVISION 0.8", "# RHEV REVISION 0.9",
                 "# RHEV REVISION 1.0"]

    _MPATH_CONF_TAG = "# RHEV REVISION 1.1"

    # Having the PRIVATE_TAG in the conf file means
    # vdsm-tool should never change the conf file
    # even when using the --force flag
    _MPATH_CONF_PRIVATE_TAG = "# RHEV PRIVATE"

    _MPATH_CONF_TEMPLATE = _MPATH_CONF_TAG + _STRG_MPATH_CONF

    _scsi_id = utils.CommandPath("scsi_id",
                                 "/usr/lib/udev/scsi_id",  # Fedora
                                 "/lib/udev/scsi_id",  # EL6, Ubuntu
                                 )

    @property
    def name(self):
        return 'multipath'

    @property
    def services(self):
        '''
        If multipathd is up, it will be reloaded after configuration,
        or started before vdsm starts, so service should not be stopped
        during configuration.
        '''
        return []

    def configure(self):
        """
        Set up the multipath daemon configuration to the known and
        supported state. The original configuration, if any, is saved
        """

        if os.path.exists(self._MPATH_CONF):
            backup = self._MPATH_CONF + '.' + time.strftime("%Y%m%d%H%M")
            shutil.copyfile(self._MPATH_CONF, backup)
            utils.persist(backup)

        with tempfile.NamedTemporaryFile() as f:
            f.write(self._MPATH_CONF_TEMPLATE %
                    {'scsi_id_path': self._scsi_id.cmd})
            f.flush()
            cmd = [constants.EXT_CP, f.name,
                   self._MPATH_CONF]
            rc, out, err = utils.execCmd(cmd)

            if rc != 0:
                raise RuntimeError("Failed to perform Multipath config.")
        utils.persist(self._MPATH_CONF)

        # Flush all unused multipath device maps
        utils.execCmd([constants.EXT_MULTIPATH, "-F"])

        rc = service.service_reload("multipathd")
        if rc != 0:
            status = service.service_status("multipathd", False)
            if status == 0:
                raise RuntimeError("Failed to reload Multipath.")

    def isconfigured(self, *args):
        """
        Check the multipath daemon configuration. The configuration file
        /etc/multipath.conf should contain a tag in form
        "RHEV REVISION X.Y" for this check to succeed.
        If the tag above is followed by tag "RHEV PRIVATE" the configuration
        should be preserved at all cost.
        """

        if os.path.exists(self._MPATH_CONF):
            first = second = ''
            with open(self._MPATH_CONF) as f:
                mpathconf = [x.strip("\n") for x in f.readlines()]
            try:
                first = mpathconf[0]
                second = mpathconf[1]
            except IndexError:
                pass
            if self._MPATH_CONF_PRIVATE_TAG in second:
                sys.stdout.write("Manual override for multipath.conf detected"
                                 " - preserving current configuration\n")
                if self._MPATH_CONF_TAG not in first:
                    sys.stdout.write("This manual override for multipath.conf "
                                     "was based on downrevved template. "
                                     "You are strongly advised to "
                                     "contact your support representatives\n")
                return YES

            if self._MPATH_CONF_TAG in first:
                sys.stdout.write("Current revision of multipath.conf detected,"
                                 " preserving\n")
                return YES

            for tag in self._OLD_TAGS:
                if tag in first:
                    sys.stdout.write("Downrev multipath.conf detected, "
                                     "upgrade required\n")
                    return NO

        sys.stdout.write("multipath requires configuration\n")
        return NO
