# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

EXTRA_DIST = \
	after_vm_destroy.py \
	before_vm_start.py \
	sudoers.vdsm_hook_vhostmd

install-data-hook:
	chmod 440 $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_vhostmd

install-data-local: install-data-sudoers
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_start
	$(INSTALL_SCRIPT) before_vm_start.py \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_vhostmd
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_vm_destroy
	$(INSTALL_SCRIPT) after_vm_destroy.py \
		$(DESTDIR)$(vdsmhooksdir)/after_vm_destroy/50_vhostmd
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_destination
	$(LN_S) ../before_vm_start/50_vhostmd \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_destination/50_vhostmd
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_dehibernate
	$(LN_S) ../before_vm_start/50_vhostmd \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_dehibernate/50_vhostmd

install-data-sudoers:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/sudoers.d
	$(INSTALL_DATA) sudoers.vdsm_hook_vhostmd \
		$(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_vhostmd
