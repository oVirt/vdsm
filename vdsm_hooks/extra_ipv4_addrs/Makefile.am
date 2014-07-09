#
# Copyright 2014 Red Hat, Inc.
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
