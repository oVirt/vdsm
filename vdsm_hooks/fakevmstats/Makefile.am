# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

include $(top_srcdir)/build-aux/Makefile.subs

CLEANFILES = \
	config.log

EXTRA_DIST = \
	after_get_all_vm_stats.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_vm_stats
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_get_vm_stats
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_all_vm_stats
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_get_all_vm_stats
	$(INSTALL_SCRIPT) $(srcdir)/after_get_all_vm_stats.py \
		$(DESTDIR)$(vdsmhooksdir)/after_get_all_vm_stats/10_fakevmstats

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_all_vm_stats/10_fakevmstats
