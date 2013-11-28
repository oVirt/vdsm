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

include $(top_srcdir)/build-aux/Makefile.subs

vdsmglusterdir = $(vdsmdir)/gluster

nodist_vdsmgluster_PYTHON = \
	hostname.py
	$(NULL)

dist_vdsmgluster_PYTHON = \
	__init__.py \
	api.py \
	cli.py \
	exception.py \
	hooks.py \
	services.py \
	$(NULL)

dist_vdsmgluster_DATA = \
	vdsmapi-gluster-schema.json \
	$(NULL)

EXTRA_DIST = \
	hostname.py.in
	$(NULL)

CLEANFILES = \
	hostname.py
	$(NULL)

all-local: \
	$(nodist_vdsmgluster_PYTHON)
