# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import libvirt

UP = 'Up'
DOWN = 'Down'
MIGRATION_SOURCE = 'Migration Source'
MIGRATION_DESTINATION = 'Migration Destination'
PAUSED = 'Paused'
POWERING_DOWN = 'Powering down'
POWERING_UP = 'Powering up'
REBOOT_IN_PROGRESS = 'RebootInProgress'
RESTORING_STATE = 'Restoring state'
SAVING_STATE = 'Saving State'
WAIT_FOR_LAUNCH = 'WaitForLaunch'

PAUSED_STATES = (POWERING_DOWN, REBOOT_IN_PROGRESS, UP)

LIBVIRT_DOWN_STATES = (
    libvirt.VIR_DOMAIN_SHUTOFF,
    libvirt.VIR_DOMAIN_CRASHED,
)
