#
# Copyright 2012-2017 Red Hat, Inc.
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

if CONTAINERS
SUBDIRS += containers
endif

common_modules = common/*_test.py

containers_modules = containers/*_test.py

device_modules = \
	devices/parsing/complex_vm_tests.py \
	$(NULL)

network_modules = network/*_test.py

test_modules = \
	API_test.py \
	alignmentScanTests.py \
	api_response_test.py \
	bridgeTests.py \
	bulk_sampling_test.py \
	capsTests.py \
	clientifTests.py \
	cmdutilsTests.py \
	commands_test.py \
	concurrentTests.py \
	config_test.py \
	containersconnection_test.py \
	cpuinfo_test.py \
	cpuProfileTests.py \
	deviceTests.py \
	domainDescriptorTests.py \
	encodingTests.py \
	exception_test.py \
	executorTests.py \
	eventfd_test.py \
	fakesanlock_test.py \
	filecontrol_test.py \
	fuserTests.py \
	glob_test.py \
	gluster_cli_tests.py \
	gluster_exception_test.py \
	glusterTestData.py \
	guestagentTests.py \
	hooksTests.py \
	hostdevTests.py \
	hoststatsTests.py \
	hwinfo_test.py \
	jobsTests.py \
	libvirtconnectionTests.py \
	logutils_test.py \
	mkimageTests.py \
	modprobe.py \
	moduleloader_test.py \
	monkeypatchTests.py \
	momTests.py \
	momPolicyTests.py \
	numaUtilsTests.py \
	osinfo_test.py \
	osutils_test.py \
	passwordsTests.py \
	periodicTests.py \
	permutationTests.py \
	properties_test.py \
	protocoldetectorTests.py \
	pthreadTests.py \
	qemuimg_test.py \
	responseTests.py \
	rngsources_test.py \
	samplingTests.py \
	scheduleTests.py \
	schemaValidationTest.py \
	sigutils_test.py \
	sparsifyTests.py \
	sslTests.py \
	stompAdapterTests.py \
	stompAsyncClientTests.py \
	stompAsyncDispatcherTests.py \
	stompTests.py \
	storage_asyncevent_test.py \
	storage_blkdiscard_test.py \
	storage_blocksd_test.py \
	storage_blockvolume_test.py \
	storage_check_test.py \
	storage_directio_test.py \
	storage_exception_test.py \
	storage_fakelib_test.py \
	storage_filesd_test.py \
	storage_fileutil_test.py \
	storage_filevolume_test.py \
	storage_guarded_test.py \
	storage_hsm_test.py \
	storage_imagetickets_test.py \
	storage_iscsi_test.py \
	storage_lvm_test.py \
	storage_mailbox_test.py \
	storage_merge_test.py \
	storage_misc_test.py \
	storage_monitor_test.py \
	storage_mount_test.py \
	storage_outofprocess_test.py \
	storage_persistentdict_test.py \
	storage_resourcemanager_test.py \
	storage_rwlock_test.py \
	storage_sd_manifest_test.py \
	storage_sdm_copy_data_test.py \
	storage_sdm_create_volume_test.py \
	storage_sdm_indirection_tests.py \
	storage_sdm_set_volume_generation_test.py \
	storage_sdm_update_volume_test.py \
	storage_securable_test.py \
	storage_storageserver_test.py \
	storage_testlib_test.py \
	storage_volume_artifacts_test.py \
	storage_volume_metadata_test.py \
	storage_volume_test.py \
	storage_workarounds_test.py \
	storage_xlease_test.py \
	tasksetTests.py \
	testlibTests.py \
	toolTests.py \
	toolBondingTests.py \
	tool_confmeta_test.py \
	transportWrapperTests.py \
	throttledlog_test.py \
	unicode_test.py \
	utils_test.py \
	vdscliTests.py \
	vdsClientTests.py \
	vdsmapi_test.py \
	vdsmDumpChainsTests.py \
	verify.py \
	vmApiTests.py \
	vmfakelibTests.py \
	vmLeaseTests.py \
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
	API_test.py \
	alignmentScanTests.py \
	bridgeTests.py \
	clientifTests.py \
	deviceTests.py \
	devices/parsing/complex_vm_tests.py \
	encodingTests.py \
	guestagentTests.py \
	hooksTests.py \
	hostdevTests.py \
	hoststatsTests.py \
	mkimageTests.py \
	momPolicyTests.py \
	momTests.py \
	monkeypatchTests.py \
	network/iproute2_test.py \
	network/ipwrapper_test.py \
	network/link_bond_test.py \
	network/link_iface_test.py \
	network/netlink_test.py \
	network/netinfo_test.py \
	network/ovs_driver_test.py \
	network/ovs_info_test.py \
	network/tc_test.py \
	network/unified_persistence_test.py \
	numaUtilsTests.py \
	periodicTests.py \
	protocoldetectorTests.py \
	qemuimg_test.py \
	samplingTests.py \
	schemaValidationTest.py \
	stompAdapterTests.py \
	stompAsyncClientTests.py \
	stompAsyncDispatcherTests.py \
	stompTests.py \
	storage_blkdiscard_test.py \
	storage_blocksd_test.py \
	storage_blockvolume_test.py \
	storage_fakelib_test.py \
	storage_filesd_test.py \
	storage_filevolume_test.py \
	storage_guarded_test.py \
	storage_hsm_test.py \
	storage_imagetickets_test.py \
	storage_iscsi_test.py \
	storage_lvm_test.py \
	storage_mailbox_test.py \
	storage_merge_test.py \
	storage_misc_test.py \
	storage_monitor_test.py \
	storage_mount_test.py \
	storage_outofprocess_test.py \
	storage_persistentdict_test.py \
	storage_resourcemanager_test.py \
	storage_rwlock_test.py \
	storage_sd_manifest_test.py \
	storage_sdm_copy_data_test.py \
	storage_sdm_create_volume_test.py \
	storage_sdm_indirection_tests.py \
	storage_sdm_set_volume_generation_test.py \
	storage_sdm_update_volume_test.py \
	storage_storageserver_test.py \
	storage_testlib_test.py \
	storage_volume_artifacts_test.py \
	storage_volume_metadata_test.py \
	storage_volume_test.py \
	storage_workarounds_test.py \
	toolBondingTests.py \
	unicode_test.py \
	utils_test.py \
	v2vTests.py \
	vdscliTests.py \
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

CLEANFILES = \
	$(nodist_vdsmtests_PYTHON) \
	$(dist_noinst_DATA) \
	$(dist_noinst_SCRIPTS) \
	$(NULL)

EXTRA_DIST = \
	makecert.sh \
	py-watch \
	tests_child.py \
	$(NULL)

server_certificates = \
	server.crt \
	server.csr \
	server.key \
	server.p12

other_certificates = \
	other.crt \
	other.csr \
	other.key

dist_noinst_DATA = \
	$(server_certificates) \
	$(other_certificates)

dist_noinst_SCRIPTS = \
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
	fake-virt-v2v.err \
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
	check_imports.py \
	hookValidation.py \
	fakelib.py \
	fakesanlock.py \
	fake-virt-v2v \
	fake-ssh-add \
	fake-ssh-agent \
	monkeypatch.py \
	storagefakelib.py \
	storagetestlib.py \
	testlib.py \
	testrunner.py \
	testValidation.py \
	vmfakecon.py \
	vmfakelib.py \
	$(NULL)

dist_vdsmtests_SCRIPTS = \
	run_tests.sh \
	testValidation.py \
	$(NULL)

all-local: \
	$(nodist_vdsmtests_PYTHON)

$(server_certificates) $(other_certificates): make_certificates

.PHONY: make_certificates
make_certificates: makecert.sh
	./makecert.sh

run_modules  = $(test_modules)
if CONTAINERS
run_modules += $(containers_modules)
endif
run_modules += $(network_modules)
run_modules += $(device_modules)
run_modules += $(common_modules)

# Disable builtin (recursive) check target.
check-recursive:
	@true

check-net:
	@echo '*** Running networking tests. ***'
	@$(top_srcdir)/tests/run_tests_local.sh $(network_modules)

check:
	@echo '*** Running tests.  To skip this step place NOSE_EXCLUDE=.* ***'
	@echo '*** into your environment.  Do not submit untested code!    ***'
	$(top_srcdir)/tests/run_tests_local.sh $(run_modules)
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

# Disable builtin %.out rule, overrwriting out files
%.out: ;

run_%.sh: run_%.sh.in
	@echo "  SED $@"; $(PATHSUBST) $< |$(CONFIGSUBST) >$@
	chmod a+x "$@"
