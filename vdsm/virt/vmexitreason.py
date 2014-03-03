#
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
    LIBVIRT_DOMAIN_MISSING: 'Failed to find the libvirt domain'
}
