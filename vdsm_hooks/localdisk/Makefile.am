# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

EXTRA_DIST = \
	12-vdsm-localdisk.rules \
	after_disk_prepare \
	localdisk-helper \
	sudoers.vdsm_hook_localdisk \
	before_vm_migrate_source \
	$(NULL)

UDEV_RULES_DIR = /usr/lib/udev/rules.d

install-data-local: install-data-sudoers
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_disk_prepare
	$(INSTALL_SCRIPT) $(srcdir)/after_disk_prepare \
		$(DESTDIR)$(vdsmhooksdir)/after_disk_prepare/localdisk
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_source
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_migrate_source \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_source/localdisk
	$(INSTALL_SCRIPT) $(srcdir)/localdisk-helper \
		$(DESTDIR)$(vdsmexecdir)/localdisk-helper
	$(MKDIR_P) $(DESTDIR)$(UDEV_RULES_DIR)
	$(INSTALL_DATA) $(srcdir)/12-vdsm-localdisk.rules \
		$(DESTDIR)$(UDEV_RULES_DIR)/12-vdsm-localdisk.rules

uninstall-local: uninstall-data-sudoers
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_disk_prepare/localdisk
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_source/localdisk
	$(RM) $(DESTDIR)$(vdsmexecdir)/localdisk-helper
	$(RM) $(DESTDIR)$(UDEV_RULES_DIR)/12-vdsm-localdisk.rules

install-data-sudoers:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/sudoers.d
	$(INSTALL_DATA) -m 440 $(srcdir)/sudoers.vdsm_hook_localdisk \
		$(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_localdisk

uninstall-data-sudoers:
	$(RM) $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_localdisk

