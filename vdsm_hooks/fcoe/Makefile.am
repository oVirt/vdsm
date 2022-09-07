# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

EXTRA_DIST = \
	85-vdsm-hook-fcoe.preset \
	fcoe_before_network_setup.py \
	$(NULL)

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_network_setup
	$(INSTALL_SCRIPT) $(srcdir)/fcoe_before_network_setup.py \
		$(DESTDIR)$(vdsmhooksdir)/before_network_setup/50_fcoe
	$(INSTALL_DATA) $(srcdir)/85-vdsm-hook-fcoe.preset \
	    $(DESTDIR)/usr/lib/systemd/system-preset/

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_network_setup/50_fcoe
	$(RM) $(DESTDIR)/usr/lib/systemd/system-preset/85-vdsm-hook-fcoe.preset
