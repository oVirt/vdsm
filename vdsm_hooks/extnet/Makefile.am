# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

EXTRA_DIST = \
	extnet_vnic.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/extnet_vnic.py \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/50_extnet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/extnet_vnic.py \
		$(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_extnet

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/50_extnet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_extnet

