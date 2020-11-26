# vdsm I/O timeouts

Some storage servers need longer I/O timeouts in failover or upgrade
scenarios, and need modified vdsm and multipath configuration.

In this example we assume that our storage ``FooIO`` needs 120 seconds
I/O timeout.


## How I/O timeouts affect system behavior?

Using larger I/O timeouts makes the system more resilient to short
storage outage. VMs will continue to run longer without pausing when
storage server is not accessible.

On the other hand, larger timeouts will cause commands run by vdsm to
block on non-responsive storage for longer time, which may cause
timeouts in vdsm API calls or internal flows even for unrelated storage.

When failing over HA VMs to another host, larger timeouts will slow down
the failover process, since sanlock must wait more time when acquiring
an expired storage lease.

When acquiring the host id during host activation, required for running
HA VMs with storage leases, larger timeouts will make the process
slower, in particular after unclean host shutdown.

oVirt is tested only with the default sanlock:io_timeout. You should use
the configuration recommended and tested by your storage vendor.


## How multipath timeouts are related to sanlock timeouts?

For best results, you need to keep multipath and sanlock timeouts
synchronized.

If multipath is using a shorter timeout, HA VM with a storage lease may
pause before the lease expire. When the VM pause, libvirt releases the
storage lease. When the lease expire, sanlock will not terminate the HA
VM. This will delay starting the HA VM on another host.

If multipath is using longer timeout, I/O to storage will continue to
block even after storage leases on this storage have expired. Processes
may be block on storage in uninterruptible state (D state). This will
delay and fail vdsm API calls or internal flows.

In the worst case, processes holding a storage leases cannot be
terminated by sanlock 60 seconds after the storage lease was expired. In
this case the host watchdog will reboot the host.

Here are some possible combinations:

| effective timeout           |   80 |  120 |  160 |
|-----------------------------|------|------|------|
| sanlock:io_timeout          |   10 |   15 |   20 |
| multipath/no_path_retry[1]  |   16 |   24 |   32 |

[1] Using 5 seconds polling_interval.


## Configuring vdsm

To configure sanlock to use longer I/O timeout, we need to configure
vdsm, since vdsm is managing sanlock.

For each host, install this vdsm configuration drop-in file:

    $ cat /etc/vdsm/vdsm.conf.d/99-FooIO.conf
    # Configuration for FooIO storage.

    [sanlock]
    # Set renewal timeout to 120 seconds
    # (8 * io_timeout == 120).
    io_timeout = 15


## Configuring multipath

When using longer sanlock:io_timeout in vdsm, we need to update
multipath to use larger no_path_retry value.

For each host, install this multipath configuration drop-in file:

    $ cat /etc/multipath/conf.d/FooIO.conf
    # Configuration for FooIO storage.

    overrides {
        # Queue I/O for 120 seconds when all paths fail
        # (no_path_retry * polling_interval == 120).
        no_path_retry 24
    }


## Configuring hosted engine agent

Additional configuration may be needed for hosted engine agent, which
uses sanlock for protecting the storage whiteboard.

Please see hosted engine documentation for more info.


## Configuring ovirt-engine

Additional configuration may be needed for ovirt-engine, to compensate
for the longer timeouts.

Please see ovirt-engine documentation for more info.


## Upgrading a host

Take the following steps for each host:

1. Move host to maintenance.
2. Upgrade vdsm to version 4.40.39 or later
3. Reload multipathd service
4. Activate the host


## Sanlock logs

When sanlock acquires the host id for the first time using a new
configuration, we will see this log:

    2020-11-26 23:07:32 64887 [7814]: s19 lockspace
    84dc4e3c-00fd-4263-84e8-fc246eeee6e9:2:/dev/84dc4e3c-00fd-4263-84e8-fc246eeee6e9/ids:0
    2020-11-26 23:07:32 64887 [159299]: s19 delta_acquire other_io_timeout 10 our 15

This means that the last host using host id 2 on this lockspace
was using 10 seconds io_timeout. This host is using 15 seconds
io_timeout now.

When sanlock is failing to renew a lease, we will see this log:

    2020-11-27 00:13:22 69604 [234701]: s22 delta_renew read timeout 15
    sec offset 0 /dev/06e43bfc-2ffe-419e-843b-59a5d23165e1/ids

The log shows that we use io_timeout of 15 seconds.


## Additional info

- sanlock timeouts
  https://pagure.io/sanlock/raw/master/f/src/timeouts.h

- multipath configuration
  https://linux.die.net/man/5/multipath.conf
