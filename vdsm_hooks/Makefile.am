# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

SUBDIRS = \
	cpuflags \
	ethtool_options \
	faqemu \
	fcoe \
	localdisk \
	log_console \
	log_firmware \
	openstacknet \
	qemucmdline \
	vhostmd \
	vmfex_dev

include $(top_srcdir)/build-aux/Makefile.subs

# Additional hooks
if HOOKS
SUBDIRS += \
	allocate_net \
	boot_hostdev \
	checkimages \
	checkips \
	diskunmap \
	extnet \
	extra_ipv4_addrs \
	fileinject \
	fakevmstats \
	httpsisoboot \
	macbind \
	nestedvt \
	scratchpad \
	smbios \
	spiceoptions \
	$(NULL)
endif

CLEANFILES = \
	config.log \
	$(nodist_vdsmexec_SCRIPTS)

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
	after_disk_prepare \
	before_disk_hotplug \
	after_disk_hotplug \
	before_disk_hotunplug \
	after_disk_hotunplug \
	before_vdsm_start \
	after_vdsm_stop \
	before_network_setup \
	after_network_setup \
	after_network_setup_fail \
	before_set_num_of_cpus \
	after_set_num_of_cpus \
	before_get_vm_stats \
	after_get_vm_stats \
	before_get_all_vm_stats \
	after_get_all_vm_stats \
	before_get_caps \
	after_get_caps \
	before_get_stats \
	after_get_stats \
	after_hostdev_list_by_caps \
	before_memory_hotplug \
	after_memory_hotplug \
	$(NULL)

all-local: \
	$(nodist_vdsmexec_SCRIPTS)

install-data-local:
	(for hook in $(VDSMHOOKS); do \
	    $(MKDIR_P) $(DESTDIR)$(vdsmexecdir)/hooks/$$hook; \
	done)
