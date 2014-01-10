#
# Copyright 2008-2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

SUBDIRS = faqemu vhostmd qemucmdline openstacknet

include $(top_srcdir)/build-aux/Makefile.subs

# Additional hooks
if HOOKS
SUBDIRS += \
	checkimages \
	directlun \
	extnet \
	fileinject \
	floppy \
	hostusb \
	hugepages \
	isolatedprivatevlan \
	macspoof \
	nestedvt \
	numa \
	pincpu \
	promisc \
	qos \
	scratchpad \
	smbios \
	sriov \
	vmdisk \
	vmfex \
	vmfex_dev \
	$(NULL)
endif

dist_vdsmexec_SCRIPTS = \
	unpersist-vdsm-hook

nodist_vdsmexec_SCRIPTS = \
	persist-vdsm-hooks

CLEANFILES = \
	config.log \
	$(nodist_vdsmexec_SCRIPTS)

EXTRA_DIST = \
	persist-vdsm-hooks.in

VDSMHOOKS = \
	before_device_create \
	after_device_create \
	before_device_destroy \
	after_device_destroy \
	before_vm_start \
	after_vm_start \
	before_vm_cont \
	after_vm_cont \
	before_vm_pause \
	after_vm_pause \
	before_vm_hibernate \
	after_vm_hibernate \
	before_vm_dehibernate \
	after_vm_dehibernate \
	before_device_migrate_source \
	after_device_migrate_source \
	before_device_migrate_destination \
	after_device_migrate_destination \
	before_vm_migrate_source \
	after_vm_migrate_source \
	before_vm_migrate_destination \
	after_vm_migrate_destination \
	before_vm_destroy \
	after_vm_destroy \
	before_vm_set_ticket \
	after_vm_set_ticket \
	before_update_device \
	after_update_device \
	after_update_device_fail \
	before_nic_hotplug \
	after_nic_hotplug \
	before_nic_hotunplug \
	after_nic_hotunplug \
	after_nic_hotplug_fail \
	after_nic_hotunplug_fail \
	before_disk_hotplug \
	after_disk_hotplug \
	before_disk_hotunplug \
	after_disk_hotunplug \
	before_vdsm_start \
	after_vdsm_stop \
	before_network_setup \
	after_network_setup \
	$(NULL)

all-local: \
	$(nodist_vdsmexec_SCRIPTS)

install-data-local:
	(for hook in $(VDSMHOOKS); do \
	    $(MKDIR_P) $(DESTDIR)$(vdsmexecdir)/hooks/$$hook; \
	done)
