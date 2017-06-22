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
	  virttests \
	  $(NULL)

if CONTAINERS
SUBDIRS += containers
endif

common_modules = common/*_test.py

containers_modules = containers/*_test.py

device_modules = \
	devices/parsing/complex_vm_test.py \
	$(NULL)

network_modules = network/*_test.py

virt_modules = virttests/*_test.py

test_modules = \
	API_test.py \
	alignmentscan_test.py \
	api_response_test.py \
	bridge_test.py \
	caps_test.py \
	clientif_test.py \
	cmdutils_test.py \
	commands_test.py \
	config_test.py \
	cpu_profile_test.py \
	cpuinfo_test.py \
	device_test.py \
	encoding_test.py \
	exception_test.py \
	executor_test.py \
	eventfd_test.py \
	fakesanlock_test.py \
	fallocate_test.py \
	filecontrol_test.py \
	fuser_test.py \
	glob_test.py \
	gluster_cli_test.py \
	gluster_exception_test.py \
	glusterTestData.py \
	guestagent_test.py \
	hooks_test.py \
	hostdev_test.py \
	hoststats_test.py \
	hugepages_test.py \
	hwinfo_test.py \
	jobs_test.py \
	libvirtconnection_test.py \
	loopback_test.py \
	mkimage_test.py \
	modprobe.py \
	moduleloader_test.py \
	monkeypatch_test.py \
	mom_test.py \
	mompolicy_test.py \
	osinfo_test.py \
	osutils_test.py \
	passwords_test.py \
	periodic_test.py \
	permutation_test.py \
	properties_test.py \
	protocoldetector_test.py \
	qemuimg_test.py \
	response_test.py \
	rngsources_test.py \
	sampling_test.py \
	schedule_test.py \
	schemavalidation_test.py \
	sigutils_test.py \
	sparsify_test.py \
	ssl_test.py \
	stompadapter_test.py \
	stompasyncclient_test.py \
	stompasyncdispatcher_test.py \
	stomp_test.py \
	storage_asyncevent_test.py \
	storage_asyncutils_test.py \
	storage_blkdiscard_test.py \
	storage_blockdev_test.py \
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
	storage_image_test.py \
	storage_imagetickets_test.py \
	storage_iscsi_test.py \
	storage_lvm_test.py \
	storage_mailbox_test.py \
	storage_merge_test.py \
	storage_misc_test.py \
	storage_monitor_test.py \
	storage_mount_test.py \
	storage_operation_test.py \
	storage_outofprocess_test.py \
	storage_persistentdict_test.py \
	storage_qcow2_test.py \
	storage_resourcemanager_test.py \
	storage_rwlock_test.py \
	storage_sd_manifest_test.py \
	storage_sdm_amend_volume_test.py \
	storage_sdm_copy_data_test.py \
	storage_sdm_create_volume_test.py \
	storage_sdm_indirection_test.py \
	storage_sdm_merge_test.py \
	storage_sdm_update_volume_test.py \
	storage_securable_test.py \
	storage_storageserver_test.py \
	storage_sysfs_test.py \
	storage_testlib_test.py \
	storage_volume_artifacts_test.py \
	storage_volume_metadata_test.py \
	storage_volume_test.py \
	storage_workarounds_test.py \
	storage_xlease_test.py \
	taskset_test.py \
	testlib_test.py \
	tool_confmeta_test.py \
	tool_test.py \
	throttledlog_test.py \
	unicode_test.py \
	utils_test.py \
	validate_test.py \
	vdsmapi_test.py \
	vdsmdumpchains_test.py \
	verify.py \
	vmapi_test.py \
	vmfakelib_test.py \
	vmmigration_test.py \
	vmoperations_test.py \
	vmrecovery_test.py \
	vmsecret_test.py \
	vmstorage_test.py \
	vm_test.py \
	vmTestsData.py \
	vmutils_test.py \
	vmxml_test.py \
	v2v_test.py \
	zombiereaper_test.py \
	$(NULL)

blacklist_modules_python3 = \
	API_test.py \
	alignmentscan_test.py \
	bridge_test.py \
	clientif_test.py \
	device_test.py \
	devices/parsing/complex_vm_test.py \
	encoding_test.py \
	guestagent_test.py \
	hooks_test.py \
	hostdev_test.py \
	hoststats_test.py \
	hugepages_test.py \
	loopback_test.py \
	mkimage_test.py \
	mompolicy_test.py \
	mom_test.py \
	monkeypatch_test.py \
	network/ipwrapper_test.py \
	network/netinfo_test.py \
	network/tc_test.py \
	periodic_test.py \
	protocoldetector_test.py \
	qemuimg_test.py \
	sampling_test.py \
	schemavalidation_test.py \
	stompadapter_test.py \
	stompasyncclient_test.py \
	stompasyncdispatcher_test.py \
	stomp_test.py \
	storage_blkdiscard_test.py \
	storage_blocksd_test.py \
	storage_blockvolume_test.py \
	storage_fakelib_test.py \
	storage_filesd_test.py \
	storage_filevolume_test.py \
	storage_guarded_test.py \
	storage_hsm_test.py \
	storage_image_test.py \
	storage_imagetickets_test.py \
	storage_iscsi_test.py \
	storage_lvm_test.py \
	storage_mailbox_test.py \
	storage_merge_test.py \
	storage_misc_test.py \
	storage_monitor_test.py \
	storage_outofprocess_test.py \
	storage_persistentdict_test.py \
	storage_qcow2_test.py \
	storage_resourcemanager_test.py \
	storage_rwlock_test.py \
	storage_sd_manifest_test.py \
	storage_sdm_amend_volume_test.py \
	storage_sdm_copy_data_test.py \
	storage_sdm_create_volume_test.py \
	storage_sdm_indirection_test.py \
	storage_sdm_merge_test.py \
	storage_sdm_update_volume_test.py \
	storage_storageserver_test.py \
	storage_testlib_test.py \
	storage_volume_artifacts_test.py \
	storage_volume_metadata_test.py \
	storage_volume_test.py \
	storage_workarounds_test.py \
	unicode_test.py \
	utils_test.py \
	v2v_test.py \
	vmapi_test.py \
	vmmigration_test.py \
	vmoperations_test.py \
	vmrecovery_test.py \
	vmsecret_test.py \
	vmstorage_test.py \
	vm_test.py \
	vmutils_test.py \
	vmxml_test.py \
	vmfakelib_test.py \
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
	toolTests_CCPP.conf \
	toolTests_abrt-action-save-package-data.conf \
	toolTests_abrt.conf \
	toolTests_empty.conf \
	toolTests_lconf_ssl.conf \
	toolTests_libvirtd.conf \
	toolTests_mtab_1g \
	toolTests_mtab_default \
	toolTests_mtab_nohugetlbfs \
	toolTests_qemu_sanlock.conf \
	toolTests_qemu_ssl.conf \
	toolTests_vdsm_no_ssl.conf \
	toolTests_vdsm_ssl.conf \
	toolTests_vmcore.conf \
	$(NULL)

dist_vdsmtests_PYTHON = \
	$(test_modules) \
	apiData.py \
	check_imports.py \
	hookValidation.py \
	fakelib.py \
	fakesanlock.py \
	loopback.py \
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
	fake-virt-sysprep \
	fake-virt-v2v \
	fake-ssh-add \
	fake-ssh-agent \
	run_tests.sh \
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
run_modules += $(virt_modules)

# Disable builtin (recursive) check target.
check-recursive:
	@true

check-net:
	@echo '*** Running networking tests. ***'
	@$(top_srcdir)/tests/run_tests_local.sh $(network_modules)

check-virt:
	@echo '*** Running virt tests. ***'
	@$(top_srcdir)/tests/run_tests_local.sh $(virt_modules)

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
