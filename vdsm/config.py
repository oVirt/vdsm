#
# Copyright 2011 Red Hat, Inc.
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

# for a "singleton" config object
import ConfigParser
import constants

config = ConfigParser.ConfigParser()
#####################################################################
config.add_section('vars')
#####################################################################
#Enable core dump
config.set('vars', 'core_dump_enable', 'true')
#This reserves memory for the host to prevent the
#VMs from using all the physical pages.
#The values are in Mbytes
config.set('vars', 'host_mem_reserve', '256')
config.set('vars', 'guest_ram_overhead', '65')
#memory reserved for non-vds-administered programs
config.set('vars', 'extra_mem_reserve', '0')

#NIC model is rtl8139, ne2k_pci pv or any other valid device recognized by kvm/qemu
#if a coma separated list given then a NIC per device will be created
config.set('vars', 'nic_model', 'rtl8139,pv')
# migration_timeout: maximum time the destination waits for migration to end.
# source waits twice as long (to avoid races)
config.set('vars', 'migration_timeout', '300')
# time to wait (in seconds) for migration destination to start listening before
# migration begins.
config.set('vars', 'migration_listener_timeout', '30')
# maximum bandwidth for migration, in mbps. 0 means libvirts' default (30mbps?)
config.set('vars', 'migration_max_bandwidth', '0')
# how often (in seconds) should the monitor thread pulse. 0 means the thread is disabled.
config.set('vars', 'migration_monitor_interval', '10')

# comma-separated list of fnmatch-patterns for host nics to be hidden from vdsm
config.set('vars', 'hidden_nics', 'wlan*,usb*')

# maxmium allowed downtime for live migration in milliseconds (anything below
# 100ms is ignored) if you do not care about liveness of migration, set to a
# very high value, such as 600000
config.set('vars', 'migration_downtime', '500')
# incremental steps used to reach migration_downtime
config.set('vars', 'migration_downtime_steps', '10')

# maximum concurrent outgoing migrations
config.set('vars', 'max_outgoing_migrations', '5')

#Destroy/Shutdown timeouts defines timeout (in sec) before complete VM destroying/shutdowning
config.set('vars', 'sys_shutdown_timeout', '10')
config.set('vars', 'user_shutdown_timeout', '30')
# time (in sec) to wait for guest agent. default is 30 seconds
config.set('vars', 'guest_agent_timeout', '30')

# time to wait (in seconds) for vm to respond to a monitor command.
# 30 secs is a nice default.
# set to 300 if the vm is expected to freeze during cluster failover
config.set('vars', 'vm_command_timeout', '60')

# how often should we sample each vm for statistics (seconds)
config.set('vars', 'vm_watermark_interval', '2')
config.set('vars', 'vm_sample_cpu_interval', '15')
config.set('vars', 'vm_sample_cpu_window', '2')
config.set('vars', 'vm_sample_disk_interval', '60')
config.set('vars', 'vm_sample_disk_window', '2')
config.set('vars', 'vm_sample_disk_latency_interval', '60')
config.set('vars', 'vm_sample_disk_latency_window', '2')
config.set('vars', 'vm_sample_net_interval', '5')
config.set('vars', 'vm_sample_net_window', '2')

# where the certificates and keys are situated
config.set('vars', 'trust_store_path', constants.P_TRUSTSTORE)
# whether to use ssl encryption and authentication. default is true
config.set('vars', 'ssl', 'true')

config.set('vars', 'vds_responsiveness_timeout', '60')

config.set('vars', 'vdsm_nice', '-5')

config.set('vars', 'qemu_drive_cache', 'none')

config.set('vars', 'fake_kvm_support', 'false')

config.set('vars', 'max_open_files', '4096')

#####################################################################
config.add_section('ksm')
#####################################################################

config.set('ksm', 'ksm_monitor_thread', 'true')

#####################################################################
config.add_section('irs')
#####################################################################
config.set('irs', 'irs_enable', 'true')


#Image repository
config.set('irs', 'repository', '/rhev/data-center')
config.set('irs', 'hsm_tasks', '%(repository)s/hsm-tasks')
config.set('irs', 'images', '/images')
config.set('irs', 'irsd', '%(images)s/irsd')
#Image repository check period [seconds]
config.set('irs', 'images_check_times', '0')

config.set('irs', 'volume_utilization_percent', '50')
config.set('irs', 'volume_utilization_chunk_mb', '1024')

# how often should volume's size be checked (seconds
config.set('irs', 'vol_size_sample_interval', '60')

# StorageDomain Validate Timeout
# sd_validate_timeout = n
# n - maximum number of seconds to wait until all the domains will be validated
# Default: 80 - wait up to 80 seconds
#
config.set('irs', 'sd_validate_timeout', '80')
#
# Storage Domain Health Check delay
# The amount of seconds to wait between two successive run of the domain health check
# sd_health_check_delay = n
# Default: 10 (secs)
config.set('irs', 'sd_health_check_delay', '10')

#
# NFS mount options
# nfs_mount_options = comma-separated list (NB no white space allowed !)
#
# Default: soft,timeo=600,retrans=6,nosharecache
#
config.set('irs', 'nfs_mount_options', 'soft,timeo=600,retrans=6,nosharecache,vers=3')

config.set('irs', 'pools_data_dir', constants.P_STORAGEPOOLS)
config.set('irs', 'vol_extend_policy', 'ON')
config.set('irs', 'lock_util_path', constants.P_VDSM_LIBEXEC)
config.set('irs', 'lock_cmd', 'spmprotect.sh')
config.set('irs', 'free_lock_cmd', 'spmstop.sh')
config.set('irs', 'thread_pool_size', '10')
config.set('irs', 'max_tasks', '500')
config.set('irs', 'lvm_dev_whitelist', '')
config.set('irs', 'md_backup_versions', '30')
config.set('irs', 'md_backup_dir', constants.P_VDSM_BACKUP)
# Note: the number of pvs per vg has a hard-coded limit of 10
config.set('irs', 'maximum_allowed_pvs', '8')

config.set('irs', 'repo_stats_cache_refresh_timeout', '300')

config.set('irs', 'task_resource_default_timeout', '120000')
config.set('irs', 'prepare_image_timeout', '600000')
config.set('irs', 'gc_blocker_force_collect_interval', '60')

config.set('irs', 'maximum_domains_in_pool', '100')
# Process Pool Configuration
config.set('irs', 'process_pool_size', '100')
config.set('irs', 'process_pool_timeout', '60')
config.set('irs', 'process_pool_grace_period', '2')
config.set("irs", "process_pool_max_slots_per_domain", '10')

#####################################################################
config.add_section('addresses')
#####################################################################
#Port on which the vdsmd XMPRPC server listens to network clients
config.set('addresses', 'management_port', '54321')
config.set('addresses', 'management_ip', '')
config.set('addresses', 'guests_gateway_ip', '')

### and finally, hide defaults with local definitions ###
config.read([constants.P_VDSM_CONF + 'vdsm.conf'])
