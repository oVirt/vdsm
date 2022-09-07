# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

EXTRA_DIST = \
	before_vm_start.py \
	before_vm_migrate_source.py \
	after_vm_destroy.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_start
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_start.py \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_scratchpad
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_source
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_migrate_source.py \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_source/50_scratchpad
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_vm_destroy
	$(INSTALL_SCRIPT) $(srcdir)/after_vm_destroy.py \
		$(DESTDIR)$(vdsmhooksdir)/after_vm_destroy/50_scratchpad

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_scratchpad
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_source/50_scratchpad
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_vm_destroy/50_scratchpad
