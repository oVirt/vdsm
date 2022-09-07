# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

include $(top_srcdir)/build-aux/Makefile.subs

nodist_noinst_DATA = \
	vdsm-tmpfiles.d.conf \
	$(NULL)

CLEANFILES = \
	config.log \
	$(nodist_noinst_DATA) \
	$(NULL)

EXTRA_DIST = \
	vdsm-tmpfiles.d.conf.in \
	unlimited-core.conf \
	$(NULL)

all-local: \
	$(nodist_noinst_DATA) \
	$(NULL)

install-data-local: vdsm-tmpfiles.d.conf
	$(MKDIR_P) $(DESTDIR)/usr/lib/tmpfiles.d
	$(INSTALL_DATA) ./vdsm-tmpfiles.d.conf \
	    $(DESTDIR)/usr/lib/tmpfiles.d/vdsm.conf
	$(MKDIR_P) $(DESTDIR)/etc/systemd/system/libvirtd.service.d
	$(INSTALL_DATA) $(srcdir)/unlimited-core.conf \
	    $(DESTDIR)/etc/systemd/system/libvirtd.service.d/unlimited-core.conf
	$(MKDIR_P) $(DESTDIR)/usr/lib/systemd/system-preset

uninstall-local:
	$(RM) $(DESTDIR)/usr/lib/tmpfiles.d/vdsm.conf
	$(RM) $(DESTDIR)/etc/systemd/system/libvirtd.service.d/unlimited-core.conf
