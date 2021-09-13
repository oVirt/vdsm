# Copyright 2021 Red Hat, Inc.
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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Refer to the README and COPYING files for full details of the license
#

# no policy defined, CPUs from shared pool will be used
CPU_POLICY_NONE = "none"
# each vCPU is pinned to single pCPU that cannot be used by any other VM
CPU_POLICY_DEDICATED = "dedicated"
# like siblings below but only one vCPU can be assigned to each physical
# core
CPU_POLICY_ISOLATE_THREADS = "isolate-threads"
# manual CPU pinning or NUMA auto-pinning policy
CPU_POLICY_MANUAL = "manual"
# like dedicated but physical cores used by the VM are blocked from use by
# other VMs
CPU_POLICY_SIBLINGS = "siblings"
