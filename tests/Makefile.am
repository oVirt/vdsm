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
	bulk_sampling_test.py \
	cPopenTests.py \
	capsTests.py \
	clientifTests.py \
	cmdutilsTests.py \
	commands_test.py \
	concurrentTests.py \
	config_test.py \
	cpuinfo_test.py \
	cpuProfileTests.py \
	deviceTests.py \
	domainDescriptorTests.py \
	domain_manifest_test.py \
	encodingTests.py \
	exception_test.py \
	executorTests.py \
	eventfd_test.py \
	filecontrol_test.py \
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
	logutils_test.py \
	lvmTests.py \
	main.py \
	miscTests.py \
	mkimageTests.py \
	modprobe.py \
	moduleloader_test.py \
	monkeypatchTests.py \
	momTests.py \
	momPolicyTests.py \
	mountTests.py \
	numaUtilsTests.py \
	outOfProcessTests.py \
	osinfo_test.py \
	passwordsTests.py \
	periodicTests.py \
	permutationTests.py \
	persistentDictTests.py \
	protocoldetectorTests.py \
	pthreadTests.py \
	qemuimgTests.py \
	resourceManagerTests.py \
	responseTests.py \
	samplingTests.py \
	scheduleTests.py \
	schemaValidationTest.py \
	sdm_indirection_tests.py \
	securableTests.py \
	sigutils_test.py \
	sparsifyTests.py \
	sslTests.py \
	stompAdapterTests.py \
	stompAsyncClientTests.py \
	stompAsyncDispatcherTests.py \
	stompTests.py \
	storage_blkdiscard_test.py \
	storagefakelibTests.py \
	storageMailboxTests.py \
	storageServerTests.py \
	storagetestlibTests.py \
	storage_asyncevent_test.py \
	storage_check_test.py \
	storage_hsm_test.py \
	storage_monitor_test.py \
	storage_rwlock_test.py \
	storage_sdm_api_test.py \
	storage_sdm_create_volume_test.py \
	storage_volume_artifacts_test.py \
	storage_volume_metadata_test.py \
	tasksetTests.py \
	testlibTests.py \
	toolTests.py \
	toolBondingTests.py \
	tool_confmeta_test.py \
	transportWrapperTests.py \
	throttledlog_test.py \
	unicode_test.py \
	utilsTests.py \
	vdscliTests.py \
	vdsClientTests.py \
	vdsmapi_test.py \
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
	zombiereaper_test.py \
	$(NULL)

blacklist_modules_python3 = \
	alignmentScanTests.py \
	blockVolumeTests.py \
	blocksdTests.py \
	bridgeTests.py \
	bulk_sampling_test.py \
	cPopenTests.py \
	capsTests.py \
	clientifTests.py \
	commands_test.py \
	common/contextlib_test.py \
	common/network_test.py \
	deviceTests.py \
	devices/parsing/complex_vm_tests.py \
	domain_manifest_test.py \
	encodingTests.py \
	fileSDTests.py \
	fileUtilTests.py \
	fileVolumeTests.py \
	fuserTests.py \
	gluster_cli_tests.py \
	guestagentTests.py \
	hooksTests.py \
	hostdevTests.py \
	hoststatsTests.py \
	imagetickets_test.py \
	iscsiTests.py \
	libvirtconnectionTests.py \
	lvmTests.py \
	main.py \
	miscTests.py \
	mkimageTests.py \
	momPolicyTests.py \
	momTests.py \
	monkeypatchTests.py \
	mountTests.py \
	network/conf_persistence_test.py \
	network/conf_test.py \
	network/config_network_test.py \
	network/connectivity_test.py \
	network/hook_ovs_test.py \
	network/ip_test.py \
	network/iproute2_test.py \
	network/ipwrapper_test.py \
	network/link_bond_test.py \
	network/link_iface_test.py \
	network/netlink_test.py \
	network/netinfo_test.py \
	network/ovs_driver_test.py \
	network/ovs_info_test.py \
	network/ovs_test.py \
	network/qos_test.py \
	network/sourcerouting_test.py \
	network/tc_test.py \
	network/unified_persistence_test.py \
	numaUtilsTests.py \
	outOfProcessTests.py \
	periodicTests.py \
	persistentDictTests.py \
	protocoldetectorTests.py \
	qemuimgTests.py \
	resourceManagerTests.py \
	samplingTests.py \
	schemaTests.py \
	schemaValidationTest.py \
	sdm_indirection_tests.py \
	securableTests.py \
	sparsifyTests.py \
	stompAdapterTests.py \
	stompAsyncClientTests.py \
	stompAsyncDispatcherTests.py \
	stompTests.py \
	storageMailboxTests.py \
	storage_hsm_test.py \
	storage_monitor_test.py \
	storageServerTests.py \
	storage_rwlock_test.py \
	storage_blkdiscard_test.py \
	storage_sdm_api_test.py \
	storage_sdm_create_volume_test.py \
	storage_volume_artifacts_test.py \
	storage_volume_metadata_test.py \
	storagefakelibTests.py \
	storagetestlibTests.py \
	tasksetTests.py \
	testlibTests.py \
	toolBondingTests.py \
	toolTests.py \
	transportWrapperTests.py \
	unicode_test.py \
	utilsTests.py \
	v2vTests.py \
	vdsClientTests.py \
	vdscliTests.py \
	vdsmDumpChainsTests.py \
	verify.py \
	vmApiTests.py \
	vmMigrationTests.py \
	vmOperationsTests.py \
	vmRecoveryTests.py \
	vmSecretTests.py \
	vmStorageTests.py \
	vmTests.py \
	vmUtilsTests.py \
	vmXmlTests.py \
	vmfakelibTests.py \
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
	tests_child.py \
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
        glusterVolumeHealInfo.xml \
	iscsiadm_-m_iface.out \
	lvs_3386c6f2-926f-42c4-839c-38287fac8998.out \
	lvs_f9e55e18-67c4-4377-8e39-5833ca422bef.out \
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
	sdmtestlib.py \
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
	if [ "$(PYTHON3_SUPPORT)" == "1" ]; then \
		all_modules=`mktemp`; \
		blacklist=`mktemp`; \
		echo $(run_modules) | tr \  \\n |sort > "$$all_modules"; \
		echo $(blacklist_modules_python3) | tr \  \\n | sort > \
			"$$blacklist"; \
		modules=$$(comm -2 -3 "$$all_modules" "$$blacklist"); \
		rm -f "$$all_modules" "$$blacklist"; \
		PYTHON_EXE="$(PYTHON3)" \
			$(top_srcdir)/tests/run_tests_local.sh "$$modules"; \
	fi
