# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

SUBDIRS = faqemu vhostmd

dist_vdsmexec_SCRIPTS = \
	persist-vdsm-hooks \
	unpersist-vdsm-hook

VDSMHOOKS = \
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
	before_vm_migrate_source \
	after_vm_migrate_source \
	before_vm_migrate_destination \
	after_vm_migrate_destination \
	after_vm_destroy \
	before_vdsm_start \
	after_vdsm_stop

install-data-local:
	(for hook in $(VDSMHOOKS); do \
	    $(MKDIR_P) $(DESTDIR)$(vdsmexecdir)/hooks/$$hook; \
	done)
