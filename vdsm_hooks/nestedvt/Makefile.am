# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

include $(top_srcdir)/build-aux/Makefile.subs

EXTRA_DIST = \
	before_vm_start.py \
	modprobe.conf

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/modprobe.d
	$(INSTALL_DATA) $(srcdir)/modprobe.conf \
		$(DESTDIR)$(sysconfdir)/modprobe.d/vdsm-nestedvt.conf
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_start
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_start.py \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_nestedvt

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_nestedvt
