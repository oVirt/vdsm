#
# Copyright 2008-2012 Red Hat, Inc.
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

CLEANFILES = \
	deployUtil.py.in \
	deployUtil.py

#
# NOTE:
# We keep old version to allow
# old engine to use upgraded package
#

# BEGIN-VERSION-1
dist_vdsmbootstrap_SCRIPTS = \
	vds_bootstrap_complete.py \
	vds_bootstrap.py

nodist_vdsmbootstrap_SCRIPTS = \
	deployUtil.py
# END-VERSION-1

# BEGIN-VERSION-2
interface2dir=$(vdsmbootstrapdir)/interface-2
dist_interface2_SCRIPTS = \
	vds_bootstrap_complete.py \
	vds_bootstrap.py \
	setup

nodist_interface2_SCRIPTS = \
	deployUtil.py
# END-VERSION-2

deployUtil.py.in:	$(top_srcdir)/vdsm_reg/deployUtil.py.in
	ln -s $(top_srcdir)/vdsm_reg/deployUtil.py.in
