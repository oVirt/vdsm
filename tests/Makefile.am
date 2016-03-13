#
# Copyright 2012-2016 Red Hat, Inc.
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

SUBDIRS = \
	  common \
	  cpuinfo \
	  functional \
	  devices \
	  integration \
	  network \
	  $(NULL)

test_modules_py3 = \
	apiData.py \
	virt_api_test.py \
	cmdutilsTests.py \
	cpuProfileTests.py \
	sslTests.py \
	domainDescriptorTests.py \
	executorTests.py \
	passwordsTests.py \
	permutationTests.py \
	pthreadTests.py \
	responseTests.py \
	scheduleTests.py \
	vmStatsTests.py \
	$(NULL)

common_modules = common/*_test.py

device_modules = \
	devices/parsing/complex_vm_tests.py \
	$(NULL)

network_modules = network/*_test.py

test_modules = \
	alignmentScanTests.py \
	virt_api_test.py \
	blocksdTests.py \
	blockVolumeTests.py \
	bridgeTests.py \
	cPopenTests.py \
	capsTests.py \
	cpuinfo_test.py \
	clientifTests.py \
	cmdutilsTests.py \
	commands_test.py \
	concurrentTests.py \
	cpuProfileTests.py \
	deviceTests.py \
	domainDescriptorTests.py \
	domain_manifest_test.py \
	encodingTests.py \
	exception_test.py \
	executorTests.py \
	fileSDTests.py \
	fileVolumeTests.py \
	fileUtilTests.py \
	fuserTests.py \
	gluster_cli_tests.py \
	gluster_exception_test.py \
	glusterTestData.py \
	guestagentTests.py \
	hooksTests.py \
	hostdevTests.py \
	hoststatsTests.py \
	imagetickets_test.py \
	iscsiTests.py \
	jobsTests.py \
	libvirtconnectionTests.py \
	lvmTests.py \
	main.py \
	miscTests.py \
	mkimageTests.py \
	modprobe.py \
	monkeypatchTests.py \
	momTests.py \
	momPolicyTests.py \
	mountTests.py \
	numaUtilsTests.py \
	outOfProcessTests.py \
	parted_utils_tests.py \
	passwordsTests.py \
	periodicTests.py \
	permutationTests.py \
	persistentDictTests.py \
	protocoldetectorTests.py \
	pthreadTests.py \
	qemuimgTests.py \
	remoteFileHandlerTests.py \
	resourceManagerTests.py \
	responseTests.py \
	rwlock_test.py \
	samplingTests.py \
	scheduleTests.py \
	schemaTests.py \
	schemaValidationTest.py \
	sdm_indirection_tests.py \
	securableTests.py \
	sparsifyTests.py \
	sslTests.py \
	stompAdapterTests.py \
	stompAsyncClientTests.py \
	stompAsyncDispatcherTests.py \
	stompTests.py \
	storagefakelibTests.py \
	storageMailboxTests.py \
	storageMonitorTests.py \
	storageServerTests.py \
	tasksetTests.py \
	testlibTests.py \
	toolTests.py \
	toolBondingTests.py \
	transportWrapperTests.py \
	unicode_test.py \
	utilsTests.py \
	vdscliTests.py \
	vdsClientTests.py \
	vdsmDumpChainsTests.py \
	verify.py \
	vmApiTests.py \
	vmfakelibTests.py \
	vmMigrationTests.py \
	vmOperationsTests.py \
	vmRecoveryTests.py \
	vmSecretTests.py \
	vmStatsTests.py \
	vmStorageTests.py \
	vmTests.py \
	vmTestsData.py \
	vmUtilsTests.py \
	vmXmlTests.py \
	v2vTests.py \
	$(NULL)

nodist_vdsmtests_PYTHON = \
	crossImportsTests.py \
	$(NULL)

CLEANFILES = \
	$(nodist_vdsmtests_PYTHON) \
	$(dist_noinst_DATA) \
	$(NULL)

EXTRA_DIST = \
	crossImportsTests.py.in \
	makecert.sh \
	$(NULL)

dist_noinst_DATA = \
	server.crt \
	server.csr \
	server.key \
	server.p12 \
	other.crt \
	other.csr \
	other.key \
	run_tests_local.sh

dist_vdsmtests_DATA = \
	README \
	cpu_info.out \
	caps_libvirt_intel_E5649.out \
	caps_libvirt_amd_6274.out \
	caps_libvirt_ibm_S822L.out \
	caps_libvirt_ibm_S822L_le.out \
	caps_libvirt_intel_E31220.out \
	caps_libvirt_intel_E5606.out \
	caps_libvirt_intel_i73770.out \
	caps_libvirt_intel_i73770_nosnap.out \
	caps_libvirt_multiqemu.out \
	cpu_map.xml \
	caps_numactl_4_nodes.out \
	fake-virt-v2v.out \
	glusterGeoRepStatus.xml \
	glusterSnapshotConfig.xml \
	glusterSnapshotRestore.xml \
	glusterVolumeGeoRepConfigList.xml \
	glusterSnapshotList.xml \
	glusterVolumeSnapshotList.xml \
	glusterSnapshotListEmpty.xml \
	glusterVolumeSnapshotListEmpty.xml \
	glusterVolumeProfileInfo.xml \
	glusterVolumeProfileInfoNfs.xml \
	glusterVolumeRebalanceStatus.xml \
	glusterVolumeRemoveBricksStatus.xml \
	glusterVolumeTasks.xml \
	iscsiadm_-m_iface.out \
	lvs_3386c6f2-926f-42c4-839c-38287fac8998.out \
	mem_info.out \
	toolTests_empty.conf \
	toolTests_lconf_ssl.conf \
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
	fake-virt-v2v \
	fake-ssh-add \
	fake-ssh-agent \
	monkeypatch.py \
	storagefakelib.py \
	storagetestlib.py \
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

server.crt server.csr server.key other.crt other.csr other.key: makecert.sh
	./makecert.sh

run_modules  = $(test_modules)
run_modules += $(network_modules)
run_modules += $(device_modules)
run_modules += $(common_modules)

# crossImportsTests.py has to be run separate due to different tests which
# load modules first, making the env dirty. Unloading python modules
# is not supported, see http://bugs.python.org/issue9072 .
check-local:
	@echo '*** Running tests.  To skip this step place NOSE_EXCLUDE=.* ***'
	@echo '*** into your environment.  Do not submit untested code!    ***'
	$(top_srcdir)/tests/run_tests_local.sh $(args) crossImportsTests.py
	$(top_srcdir)/tests/run_tests_local.sh $(args) $(run_modules)
	PYTHON_EXE="$(PYTHON3)" $(top_srcdir)/tests/run_tests_local.sh \
		$(test_modules_py3);
