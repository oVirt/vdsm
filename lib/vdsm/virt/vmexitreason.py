# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

SUCCESS = 0
GENERIC_ERROR = 1
LOST_QEMU_CONNECTION = 2
LIBVIRT_START_FAILED = 3
MIGRATION_SUCCEEDED = 4
SAVE_STATE_SUCCEEDED = 5
ADMIN_SHUTDOWN = 6
USER_SHUTDOWN = 7
MIGRATION_FAILED = 8
LIBVIRT_DOMAIN_MISSING = 9
DESTROYED_ON_STARTUP = 10
HOST_SHUTDOWN = 11
POSTCOPY_MIGRATION_FAILED = 12
DESTROYED_ON_REBOOT = 13
DESTROYED_ON_PAUSE_TIMEOUT = 14


exitReasons = {
    SUCCESS: 'VM terminated succesfully',
    GENERIC_ERROR: 'VM terminated with error',
    LOST_QEMU_CONNECTION: 'Lost connection with qemu process',
    LIBVIRT_START_FAILED: 'failed to start libvirt vm',
    MIGRATION_SUCCEEDED: 'Migration succeeded',
    SAVE_STATE_SUCCEEDED: 'SaveState succeeded',
    ADMIN_SHUTDOWN: 'Admin shut down from the engine',
    USER_SHUTDOWN: 'User shut down from within the guest',
    MIGRATION_FAILED: 'VM failed to migrate',
    LIBVIRT_DOMAIN_MISSING: 'Failed to find the libvirt domain',
    DESTROYED_ON_STARTUP: 'VM destroyed during the startup',
    HOST_SHUTDOWN: 'VM has been terminated on the host',
    POSTCOPY_MIGRATION_FAILED: 'Migration failed in post-copy',
    DESTROYED_ON_REBOOT: 'Down as a part of the reboot process',
    DESTROYED_ON_PAUSE_TIMEOUT: 'Down because paused for too long',
}
