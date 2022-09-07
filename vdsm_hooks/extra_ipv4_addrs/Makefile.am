# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

EXTRA_DIST = \
	extra_ipv4_addrs.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_network_setup
	$(INSTALL_SCRIPT) $(srcdir)/extra_ipv4_addrs.py \
		$(DESTDIR)$(vdsmhooksdir)/after_network_setup/40_extra_ipv4_addrs

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_network_setup/40_extra_ipv4_addrs

install-data-sudoers:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/sudoers.d
	$(INSTALL_DATA) $(srcdir)/sudoers \
		$(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_extra_ipv4_addrs

uninstall-data-sudoers:
	$(RM) $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_extra_ipv4_addrs
