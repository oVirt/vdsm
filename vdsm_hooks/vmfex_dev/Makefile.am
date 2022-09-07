# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

EXTRA_DIST = \
	vmfex_vnic.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/vmfex_vnic.py \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/50_vmfex
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_migrate_destination
	$(INSTALL_SCRIPT) $(srcdir)/vmfex_vnic.py \
		$(DESTDIR)$(vdsmhooksdir)/before_device_migrate_destination/50_vmfex
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/vmfex_vnic.py \
		$(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_vmfex

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/50_vmfex
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_migrate_destination/50_vmfex
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_vmfex

