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

SUBDIRS = functional

test_modules = \
	main.py \
	alignmentScanTests.py \
	betterPopenTests.py \
	betterThreadingTests.py \
	capsTests.py \
	configNetworkTests.py \
	fileUtilTests.py \
	getAllVolumesTests.py \
	gluster_cli_tests.py \
	guestIFTests.py \
	hooksTests.py \
	lsblkTests.py \
	md_utils_tests.py \
	miscTests.py \
	mountTests.py \
	netinfoTests.py \
	parted_utils_tests.py \
	persistentDictTests.py \
	processPoolTests.py \
	resourceManagerTests.py

dist_noinst_DATA = \
	run_tests_local.sh \
	cpu_info.out \
	route_info.out \
	glob_1c60971a-8647-44ac-ae33-6520887f8843.out \
	lvs_3386c6f2-926f-42c4-839c-38287fac8998.out

dist_vdsmtests_DATA = \
	README \
	netmaskconversions

dist_vdsmtests_PYTHON = \
	$(test_modules) \
	testrunner.py \
	testValidation.py

dist_vdsmtests_SCRIPTS = \
	run_tests.sh \
	testValidation.py \
	cpu_info.out

check-local:
	@echo '*** Running tests.  To skip this step place NOSE_EXCLUDE=.* ***'
	@echo '*** into your environment.  Do not submit untested code!    ***'
	$(top_srcdir)/tests/run_tests_local.sh $(test_modules)

