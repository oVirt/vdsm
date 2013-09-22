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

include $(top_srcdir)/build-aux/Makefile.subs

SUBDIRS = functional

test_modules = \
	alignmentScanTests.py \
	apiTests.py \
	betterPopenTests.py \
	capsTests.py \
	configNetworkTests.py \
	fileUtilTests.py \
	fuserTests.py \
	getAllVolumesTests.py \
	gluster_cli_tests.py \
	glusterTestData.py \
	guestIFTests.py \
	hooksTests.py \
	ipwrapperTests.py \
	netconfTests.py \
	netmodelsTests.py \
	jsonRpcTests.py \
	jsonRpcUtils.py \
	vmTests.py \
	vmTestsData.py \
	libvirtconnectionTests.py \
	lsblkTests.py \
	lvmTests.py \
	main.py \
	md_utils_tests.py \
	miscTests.py \
	mkimageTests.py \
	mountTests.py \
	netinfoTests.py \
	outOfProcessTests.py \
	parted_utils_tests.py \
	permutationTests.py \
	persistentDictTests.py \
	remoteFileHandlerTests.py \
	resourceManagerTests.py \
	schemaTests.py \
	sslTests.py \
	storageMailboxTests.py \
	tcTests.py \
	utilsTests.py \
	vdsClientTests.py \
	volumeTests.py \
	zombieReaperTests.py \
	$(NULL)

nodist_vdsmtests_PYTHON = \
	crossImportsTests.py \
	$(NULL)

CLEANFILES = \
	$(nodist_vdsmtests_PYTHON) \
	$(NULL)

EXTRA_DIST = \
	crossImportsTests.py.in \
	$(NULL)

dist_noinst_DATA = \
	run_tests_local.sh

dist_vdsmtests_DATA = \
	README \
	cpu_info.out \
	caps_libvirt_intel_E5649.out \
	caps_libvirt_amd_6274.out \
	caps_libvirt_intel_E31220.out \
	glob_1c60971a-8647-44ac-ae33-6520887f8843.out \
	glusterVolumeProfileInfo.xml \
	glusterVolumeProfileInfoNfs.xml \
	glusterVolumeRebalanceStatus.xml \
	glusterVolumeRemoveBricksStatus.xml \
	lvs_3386c6f2-926f-42c4-839c-38287fac8998.out \
	netmaskconversions \
	tc_filter_show.out \
	$(NULL)

dist_vdsmtests_PYTHON = \
	$(test_modules) \
	apiData.py \
	monkeypatch.py \
	testrunner.py \
	testValidation.py

dist_vdsmtests_SCRIPTS = \
	run_tests.sh \
	testValidation.py \
	$(NULL)

all-local: \
	$(nodist_vdsmtests_PYTHON)

# crossImportsTests.py has to be run separate due to different tests which
# load modules first, making the env dirty. Unloading python modules
# is not supported, see http://bugs.python.org/issue9072 .
check-local:
	@echo '*** Running tests.  To skip this step place NOSE_EXCLUDE=.* ***'
	@echo '*** into your environment.  Do not submit untested code!    ***'
	$(top_srcdir)/tests/run_tests_local.sh crossImportsTests.py
	$(top_srcdir)/tests/run_tests_local.sh $(test_modules)
