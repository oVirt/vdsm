# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging

from . import YES, NO, MAYBE
from vdsm import utils
from vdsm.tool import LOGGER_NAME

SEBOOL_ENABLED = "on"
SEBOOL_DISABLED = "off"

VDSM_SEBOOL_LIST = (
    "virt_use_fusefs",
    "virt_use_nfs",
    "virt_use_samba",
    "virt_use_sanlock",
    "sanlock_use_fusefs",
    "sanlock_use_nfs",
    "sanlock_use_samba",
)

log = logging.getLogger(LOGGER_NAME)


def _setup_booleans(status):
    # loading seobject is slow. Deferring its loading can reduce VDSM
    # startings time, because most utilities are and will be moved
    # to vdsm-tool.
    import seobject

    sebool_obj = seobject.booleanRecords()
    sebool_status = sebool_obj.get_all()

    sebool_obj.start()

    for sebool_variable in VDSM_SEBOOL_LIST:
        if status and not all(sebool_status[sebool_variable]):
            sebool_obj.modify(sebool_variable, SEBOOL_ENABLED)

        if not status and any(sebool_status[sebool_variable]):
            sebool_obj.modify(sebool_variable, SEBOOL_DISABLED)

    sebool_obj.finish()


def isconfigured():
    """
    True all selinux booleans in the list above are set properly
    """
    ret = YES
    if utils.get_selinux_enforce_mode() == -1:
        ret = MAYBE
        log.warning("SELinux is disabled!")
    else:
        import seobject
        sebool_obj = seobject.booleanRecords()
        sebool_status = sebool_obj.get_all()

        for sebool_variable in VDSM_SEBOOL_LIST:
            if not all(sebool_status[sebool_variable]):
                ret = NO

    return ret


def configure():
    """
    Configure selinux booleans (see list above)
    """
    if utils.get_selinux_enforce_mode() > -1:
        _setup_booleans(True)
    else:
        log.warning(
            "SELinux is disabled! "
            "Skipping SELinux boolean configuration."
        )


def removeConf():
    """
    Disabling selinux booleans (see list above)
    """
    if utils.get_selinux_enforce_mode() > -1:
        _setup_booleans(False)
    else:
        log.warning(
            "SELinux is disabled! "
            "Skipping removal of SELinux booleans."
        )
