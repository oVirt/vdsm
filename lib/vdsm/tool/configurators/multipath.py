# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import os
import sys

from vdsm.storage import mpathconf
from vdsm.storage import multipath
from vdsm.tool import service

from . import YES, NO


# If multipathd is up, it will be reloaded after configuration,
# or started before vdsm starts, so service should not be stopped
# during configuration.
services = []


def configure():
    """
    Set up the multipath daemon configuration and start the service.
    """
    backup = mpathconf.configure_multipathd()
    if backup:
        sys.stdout.write(f"Previous multipath.conf copied to {backup}\n")

    # We want to handle these cases:
    #
    # 1. multipathd is not running and the kernel module is not loaded. We
    #    don't have any online multipath devices, so there is nothing to
    #    reconfigure, but we want to make sure that multipathd is happy with
    #    new multipath configuration.
    #
    # 2. multipathd is not running but the kernel module is loaded. We may have
    #    online devices that need reconfiguration.
    #
    # 3. multipathd is running with older configuration not compatible with
    #    vdsm configuration.
    #
    # 4. multipathd is running with older vdsm configuration. Reloading is
    #    needed to update devices with new configuration.
    #
    # When we have online devices using incompatible configuration, they may
    # use "user friendly" names (/dev/mapper/mpath{N}) instead of consistent
    # names (/dev/mapper/{WWID}). Restarting multipathd with the new
    # configuration will rename the devices, however due to multipathd bug,
    # these devices may not pick all configuration changes. Reconfiguring
    # multipathd ensures that all configurations changes are applied.
    #
    # To simplify handing of all cases, we first start multipathd service. This
    # eliminates cases 1 and 2. Case 3 and 4 are handled by reconfiguring
    # multipathd.
    #
    # If any of those steps fails, we want to fail the configuration.

    service.service_start("multipathd")
    multipath.reconfigure()


def _check_mpath_metadata():
    if os.path.exists(mpathconf.CONF_FILE):
        revision, private = mpathconf.read_metadata()
        if private:
            sys.stdout.write("Manual override for multipath.conf detected"
                             " - preserving current configuration\n")
            if revision != mpathconf.REVISION_OK:
                sys.stdout.write("This manual override for multipath.conf "
                                 "was based on downrevved template. "
                                 "You are strongly advised to "
                                 "contact your support representatives\n")
            return YES

        if revision == mpathconf.REVISION_OK:
            sys.stdout.write("Current revision of multipath.conf detected,"
                             " preserving\n")
            return YES

        if revision == mpathconf.REVISION_OLD:
            sys.stdout.write("Downrev multipath.conf detected, "
                             "upgrade required\n")
            return NO

        if revision == mpathconf.REVISION_MISSING:
            sys.stdout.write("No revision of multipath.conf detected.\n")
            return NO

    sys.stdout.write("multipath requires configuration\n")
    return NO


def isconfigured():
    """
    Check the multipath daemon configuration.
    """
    conf_metadata_ok = _check_mpath_metadata()
    if conf_metadata_ok == YES:
        check_result = mpathconf.check_local_config()
        if check_result.error is not None:
            # Just warn for now, do not fail the isconfigured check here.
            sys.stdout.write(f"WARNING: {check_result.error}:\n")
            for section in check_result.issues:
                sys.stdout.write(f"  {section.name} {{\n")
                for attr in section.children:
                    sys.stdout.write(f"    {attr.key} {attr.value}\n")
                sys.stdout.write("  }\n")
            if check_result.issues:
                sys.stdout.write("This configuration is not supported and "
                                 "may lead to storage domain corruption.\n")

    return conf_metadata_ok
