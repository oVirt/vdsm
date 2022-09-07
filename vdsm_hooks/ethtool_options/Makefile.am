# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

EXTRA_DIST = \
	ethtool_options.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_network_setup
	$(INSTALL_SCRIPT) $(srcdir)/ethtool_options.py \
		$(DESTDIR)$(vdsmhooksdir)/after_network_setup/30_ethtool_options

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_network_setup/30_ethtool_options
