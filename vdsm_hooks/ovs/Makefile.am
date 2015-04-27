#
# Copyright 2015 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

utilsfile=ovs_utils.py

include $(top_srcdir)/build-aux/Makefile.subs

nodist_noinst_DATA = \
	sudoers

CLEANFILES = \
	$(nodist_noinst_DATA)

EXTRA_DIST = \
	ovs_after_get_caps.py \
	ovs_after_get_stats.py \
	ovs_before_device_create.py \
	ovs_before_network_setup.py \
	ovs_before_network_setup_ovs.py \
	ovs_before_network_setup_ip.py \
	ovs_before_network_setup_mtu.py \
	$(utilsfile) \
	sudoers.in


install-data-local: install-data-utils install-data-sudoers
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_caps
	$(INSTALL_SCRIPT) $(srcdir)/ovs_after_get_caps.py \
		$(DESTDIR)$(vdsmhooksdir)/after_get_caps/50_ovs
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_stats
	$(INSTALL_SCRIPT) $(srcdir)/ovs_after_get_stats.py \
		$(DESTDIR)$(vdsmhooksdir)/after_get_stats/50_ovs
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/ovs_before_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/50_ovs
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_network_setup
	$(INSTALL_SCRIPT) $(srcdir)/ovs_before_network_setup.py \
		$(DESTDIR)$(vdsmhooksdir)/before_network_setup/50_ovs
	$(INSTALL_SCRIPT) $(srcdir)/ovs_before_network_setup_ovs.py \
		$(DESTDIR)$(vdsmhooksdir)/before_network_setup/ovs_setup_ovs.py
	$(INSTALL_SCRIPT) $(srcdir)/ovs_before_network_setup_ip.py \
		$(DESTDIR)$(vdsmhooksdir)/before_network_setup/ovs_setup_ip.py
	$(INSTALL_SCRIPT) $(srcdir)/ovs_before_network_setup_mtu.py \
		$(DESTDIR)$(vdsmhooksdir)/before_network_setup/ovs_setup_mtu.py

uninstall-local: uninstall-data-utils uninstall-data-sudoers
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_caps/50_ovs
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_stats/50_ovs
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/50_ovs
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_network_setup/50_ovs
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_network_setup/ovs_setup_ovs.py
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_network_setup/ovs_setup_ip.py
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_network_setup/ovs_setup_mtu.py

install-data-utils:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_caps
	$(INSTALL_SCRIPT) $(srcdir)/$(utilsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_get_caps/$(utilsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_stats
	$(INSTALL_SCRIPT) $(srcdir)/$(utilsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_get_stats/$(utilsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/$(utilsfile) \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/$(utilsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_network_setup
	$(INSTALL_SCRIPT) $(srcdir)/$(utilsfile) \
		$(DESTDIR)$(vdsmhooksdir)/before_network_setup/$(utilsfile)

uninstall-data-utils:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_caps/$(utilsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_stats/$(utilsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/$(utilsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_network_setup/$(utilsfile)

install-data-sudoers:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/sudoers.d
	$(INSTALL_DATA) $(srcdir)/sudoers \
		$(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_ovs

uninstall-data-sudoers:
	$(RM) $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_ovs
