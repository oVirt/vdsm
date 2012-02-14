#
# Copyright 2008-2011 Red Hat, Inc.
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

dist_vdsm_PYTHON = \
	vdsClient.py

nodist_bin_SCRIPTS = \
	vdsClient

nodist_vdsm_PYTHON = \
	vdscli.py

dist_man1_MANS = \
	vdsClient.1

CLEANFILES = \
	config.log \
	$(nodist_bin_SCRIPTS) \
	$(nodist_vdsm_PYTHON)

EXTRA_DIST = \
	vdsClient.completion \
	vdsClient.in \
	vdscli.py.in

all-local: \
	$(nodist_bin_SCRIPTS) \
	$(nodist_vdsm_PYTHON)

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/bash_completion.d
	$(INSTALL_DATA) $(srcdir)/vdsClient.completion \
		$(DESTDIR)$(sysconfdir)/bash_completion.d/vdsClient

uninstall-local:
	$(RM) $(DESTDIR)$(sysconfdir)/bash_completion.d/vdsClient
