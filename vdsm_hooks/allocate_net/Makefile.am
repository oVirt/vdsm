# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

EXTRA_DIST = \
	before_device_create.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/before_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/10_allocate_net

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/10_allocate_net

