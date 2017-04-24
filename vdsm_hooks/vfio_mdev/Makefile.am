#
# Copyright 2017 Red Hat, Inc.
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

include $(top_srcdir)/build-aux/Makefile.subs

EXTRA_DIST = \
	before_vm_start.py \
	after_vm_destroy.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_start
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_vm_destroy
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_start.py \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_vfio_mdev
	$(INSTALL_SCRIPT) $(srcdir)/after_vm_destroy.py \
		$(DESTDIR)$(vdsmhooksdir)/after_vm_destroy/50_vfio_mdev

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_start/50_vfio_mdev
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_vm_destroy/50_vfio_mdev
