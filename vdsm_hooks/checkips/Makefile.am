# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

utilsfile=checkips_utils.py
servicefile=checkipsd
unitfile=vdsm-checkips.service

include $(top_srcdir)/build-aux/Makefile.subs

nodist_noinst_DATA = \
	vdsm-checkips.service \
	$(NULL)

CLEANFILES = \
	config.log \
	$(nodist_noinst_DATA)

EXTRA_DIST = \
	after_get_stats.py \
	vdsm-checkips.service.in \
	$(servicefile) \
	$(utilsfile)


install-data-local: install-data-utils install-data-service install-unit-service
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_stats
	$(INSTALL_SCRIPT) $(srcdir)/after_get_stats.py \
		$(DESTDIR)$(vdsmhooksdir)/after_get_stats/10_checkips

install-data-utils:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_stats
	$(INSTALL_DATA) $(srcdir)/$(utilsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_get_stats/$(utilsfile)

install-data-service:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)
	$(INSTALL_SCRIPT) $(srcdir)/$(servicefile) \
		$(DESTDIR)$(vdsmhooksdir)/$(servicefile)

install-unit-service:
	$(MKDIR_P) $(DESTDIR)$(SYSTEMD_UNIT_DIR)
	$(INSTALL_SCRIPT) -Dm 0644 ./$(unitfile) \
		$(DESTDIR)$(SYSTEMD_UNIT_DIR)/$(unitfile)

uninstall-local: uninstall-data-utils uninstall-data-service uninstall-unit-service
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_stats/10_checkips

uninstall-data-utils:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_stats/$(utilsfile)

uninstall-data-service:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/$(servicefile)

uninstall-unit-service:
	$(RM) $(DESTDIR)$(SYSTEMD_UNIT_DIR)/$(unitfile)
