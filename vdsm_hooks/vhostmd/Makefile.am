# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

EXTRA_DIST = \
	after_vm_destroy.py \
	before_vm_start.py \
	sudoers.vdsm_hook_vhostmd

install-data-hook:
	chmod 440 $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_vhostmd

install-data-local: install-data-sudoers
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_start
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_start.py \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_vhostmd
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_vm_destroy
	$(INSTALL_SCRIPT) $(srcdir)/after_vm_destroy.py \
		$(DESTDIR)$(vdsmhooksdir)/after_vm_destroy/50_vhostmd
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_destination
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_destination/50_vhostmd
	$(LN_S) ../before_vm_start/50_vhostmd \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_destination/50_vhostmd
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_dehibernate
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_dehibernate/50_vhostmd
	$(LN_S) ../before_vm_start/50_vhostmd \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_dehibernate/50_vhostmd

uninstall-local: uninstall-data-sudoers
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_vhostmd
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_vm_destroy/50_vhostmd
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_destination/50_vhostmd
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_dehibernate/50_vhostmd

install-data-sudoers:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/sudoers.d
	$(INSTALL_DATA) $(srcdir)/sudoers.vdsm_hook_vhostmd \
		$(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_vhostmd

uninstall-data-sudoers:
	$(RM) $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_vhostmd
