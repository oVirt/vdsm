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
	blocksdTests.py \
	bridgeTests.py \
	cPopenTests.py \
	capsTests.py \
	clientifTests.py \
	configNetworkTests.py \
	domainMonitorTests.py \
	domainDescriptorTests.py \
	fileVolumeTests.py \
	fileUtilTests.py \
	fuserTests.py \
	getAllVolumesTests.py \
	gluster_cli_tests.py \
	glusterTestData.py \
	guestagentTests.py \
	hooksTests.py \
	hostdevTests.py \
	iproute2Tests.py \
	ipwrapperTests.py \
	iscsiTests.py \
	jsonRpcHelper.py \
	jsonRpcTests.py \
	libvirtconnectionTests.py \
	lvmTests.py \
	main.py \
	miscTests.py \
	mkimageTests.py \
	monkeypatchTests.py \
	mountTests.py \
	netconfpersistenceTests.py \
	netconfTests.py \
	netinfoTests.py \
	netmodelsTests.py \
	numaUtilsTests.py \
	outOfProcessTests.py \
	parted_utils_tests.py \
	permutationTests.py \
	persistentDictTests.py \
	profileTests.py \
	protocoldetectorTests.py \
	qemuimgTests.py \
	qosTests.py \
	remoteFileHandlerTests.py \
	resourceManagerTests.py \
	samplingTests.py \
	schemaTests.py \
	schemaValidationTest.py \
	securableTests.py \
	sourceroutingTests.py \
	sslhelper.py \
	sslTests.py \
	storageMailboxTests.py \
	tcTests.py \
	testlibTests.py \
	toolTests.py \
	transportWrapperTests.py \
	utilsTests.py \
	vdsClientTests.py \
	vmApiTests.py \
	vmMigrationTests.py \
	vmTestsData.py \
	vmTests.py \
	vmXmlTests.py \
	volumeTests.py \
	v2vTests.py \
	$(NULL)

nodist_vdsmtests_PYTHON = \
	crossImportsTests.py \
	$(NULL)

CLEANFILES = \
	$(nodist_vdsmtests_PYTHON) \
	$(NULL)

EXTRA_DIST = \
	crossImportsTests.py.in \
	makecert.sh \
	$(NULL)

dist_noinst_DATA = \
	server.crt \
	server.csr \
	server.key \
	other.crt \
	other.csr \
	other.key \
	run_tests_local.sh

dist_vdsmtests_DATA = \
	README \
	cpu_info.out \
	caps_libvirt_intel_E5649.out \
	caps_libvirt_amd_6274.out \
	caps_libvirt_intel_E31220.out \
	caps_libvirt_intel_i73770.out \
	caps_libvirt_intel_i73770_nosnap.out \
	caps_lscpu_ppc64_1_4_4.out \
	caps_lscpu_ppc64_2_4_8.out \
	caps_numactl_4_nodes.out \
	glob_1c60971a-8647-44ac-ae33-6520887f8843.out \
	glusterVolumeProfileInfo.xml \
	glusterVolumeProfileInfoNfs.xml \
	glusterVolumeRebalanceStatus.xml \
	glusterVolumeRemoveBricksStatus.xml \
	glusterVolumeTasks.xml \
	ip_route_show_table_all.out \
	iscsiadm_-m_iface.out \
	lvs_3386c6f2-926f-42c4-839c-38287fac8998.out \
	mem_info.out \
	netmaskconversions \
	tc_filter_show.out \
	toolTests_empty.conf \
	toolTests_lconf_ssl.conf \
	toolTests_libvirt_logrotate.conf \
	toolTests_libvirtd.conf \
	toolTests_qemu_sanlock.conf \
	toolTests_qemu_ssl.conf \
	toolTests_vdsm_no_ssl.conf \
	toolTests_vdsm_ssl.conf \
	$(NULL)

dist_vdsmtests_PYTHON = \
	$(test_modules) \
	apiData.py \
	hookValidation.py \
	monkeypatch.py \
	testlib.py \
	testrunner.py \
	testValidation.py \
	vmfakelib.py \
	$(NULL)

dist_vdsmtests_SCRIPTS = \
	run_tests.sh \
	testValidation.py \
	$(NULL)

all-local: \
	$(nodist_vdsmtests_PYTHON)

server.crt:
	./makecert.sh

# crossImportsTests.py has to be run separate due to different tests which
# load modules first, making the env dirty. Unloading python modules
# is not supported, see http://bugs.python.org/issue9072 .
check-local:
	@echo '*** Running tests.  To skip this step place NOSE_EXCLUDE=.* ***'
	@echo '*** into your environment.  Do not submit untested code!    ***'
	$(top_srcdir)/tests/run_tests_local.sh crossImportsTests.py
	$(top_srcdir)/tests/run_tests_local.sh $(test_modules)
