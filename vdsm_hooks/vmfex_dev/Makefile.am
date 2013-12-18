#
# Copyright 2013 Red Hat, Inc.
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
	vmfex_vnic.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/vmfex_vnic.py \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/50_vmfex
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_migrate_destination
	$(INSTALL_SCRIPT) $(srcdir)/vmfex_vnic.py \
		$(DESTDIR)$(vdsmhooksdir)/before_device_migrate_destination/50_vmfex
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/vmfex_vnic.py \
		$(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_vmfex

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/50_vmfex
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_vmfex

