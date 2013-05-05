#
# Copyright 2012 Red Hat, Inc.
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

cpopendir = $(pyexecdir)/cpopen

dist_cpopen_PYTHON = \
	__init__.py \
	cpopen.so \
	$(NULL)

cpopen.so: cpopen.c setup.py
	(cd $(srcdir); $(PYTHON) setup.py build \
		--build-temp $(abs_builddir) --build-lib $(abs_builddir))

all-local: \
        $(dist_cpopen_PYTHON)

install-data:
	$(MKDIR_P) $(DESTDIR)$(cpopendir)
	$(INSTALL_PROGRAM) cpopen.so \
		$(DESTDIR)$(cpopendir)/cpopen.so

EXTRA_DIST = \
	cpopen.c \
	setup.py

CLEANFILES = \
	cpopen.o \
	cpopen.so
