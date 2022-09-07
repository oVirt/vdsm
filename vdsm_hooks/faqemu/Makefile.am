# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

include $(top_srcdir)/build-aux/Makefile.subs

CLEANFILES = \
	config.log

EXTRA_DIST = \
	after_get_caps.py \
	before_vm_start.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_caps
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_start
	$(INSTALL_SCRIPT) $(srcdir)/after_get_caps.py \
		$(DESTDIR)$(vdsmhooksdir)/after_get_caps/10_faqemu
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_start.py \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_start/10_faqemu

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_caps/10_faqemu
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_start/10_faqemu
