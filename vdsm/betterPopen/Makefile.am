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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

all-local: createprocess.so

.PHONY: createprocess.so createprocess.so_copy createprocess.so_build

createprocess_so_build: createprocess.c
	$(PYTHON) setup.py clean build

createprocess_so_copy: createprocess_so_build
	cp build/*/createprocess.so .

createprocess.so: createprocess_so_copy

dist_betterpopen_PYTHON = \
	__init__.py

dist_betterpopen_DATA = \
    createprocess.so

EXTRA_DIST = \
    createprocess.c \
    setup.py

CLEANFILES = \
	createprocess.so

