#
# Copyright 2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

EXTRA_DIST = \
	before_vm_start.py \
	isolatedprivatevlan-vdsm.xml

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_start
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_start.py \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_isolatedprivatevlan

	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/libvirt/nwfilter
	$(INSTALL_SCRIPT) $(srcdir)/isolatedprivatevlan-vdsm.xml \
		$(DESTDIR)$(sysconfdir)/libvirt/nwfilter/isolatedprivatevlan-vdsm.xml

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_isolatedprivatevlan
	$(RM) $(DESTDIR)/libvirt/nwfilter/isolatedprivatevlan-vdsm.xml
