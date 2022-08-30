# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import os
import logging

from vdsm.storage import mpathconf
from vdsm.storage import multipath
from vdsm.tool import service, LOGGER_NAME

from . import YES, NO


# If multipathd is up, it will be reloaded after configuration,
# or started before vdsm starts, so service should not be stopped
# during configuration.
services = []

log = logging.getLogger(LOGGER_NAME)


def configure():
    """
    Set up the multipath daemon configuration and start the service.
    """
    backup = mpathconf.configure_multipathd()
    if backup:
        log.info("Previous multipath.conf copied to %s", backup)

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
            log.info("Manual override for multipath.conf detected"
                     " - preserving current configuration")
            if revision != mpathconf.REVISION_OK:
                log.warning("This manual override for multipath.conf "
                            "was based on downrevved template. "
                            "You are strongly advised to "
                            "contact your support representatives")
            return YES

        if revision == mpathconf.REVISION_OK:
            log.info("Current revision of multipath.conf detected, preserving")
            return YES

        if revision == mpathconf.REVISION_OLD:
            log.error("Downrev multipath.conf detected, upgrade required")
            return NO

        if revision == mpathconf.REVISION_MISSING:
            log.error("No revision of multipath.conf detected.")
            return NO

    log.error("multipath requires configuration")
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
            log.warning("%s:", check_result.error)
            for section in check_result.issues:
                log.warning("  %s {", section.name)
                for attr in section.children:
                    log.warning("    %s %s", attr.key, attr.value)
                log.warning("  }")
            if check_result.issues:
                log.warning("This configuration is not supported and "
                            "may lead to storage domain corruption.")

    return conf_metadata_ok
