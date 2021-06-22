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

oVirt is tested only with the default `sanlock:io_timeout`. You should use
the configuration recommended and tested by your storage vendor.


## How multipath timeouts are related to sanlock timeouts?

For best results, you need to keep multipath and sanlock timeouts
synchronized.

If multipath is using a shorter timeout, HA VM with a sanlock lease may
pause before the lease expires. When the VM pauses, libvirt releases the
lease.  When the lease expires, sanlock will not terminate the HA VM.
This will delay starting the HA VM on another host.

If multipath is using longer timeout, I/O to storage will continue to
block even after a sanlock lease on this storage has expired. Processes
may be blocked on storage in uninterruptible state (D state). This will
delay and fail vdsm API calls or internal flows.

In the worst case, processes holding a sanlock lease cannot be
terminated by sanlock 60 seconds after the storage lease was expired. In
this case the host watchdog will reboot the host.

To configure sanlock and multipath, choose the maximum outage duration
you need to cope with. For example, your storage may need 120 seconds to
perform a failover process. You want the system to continue to run
without failures during the outage.

To handle 120 seconds outage duration, we need to configure
`sanlock:io_timeout` to 24 seconds (outage duration / 5). With this
configuration, sanlock can tolerate outage duration of 120-168 seconds
before expiring leases.

To make sure multipath does not fail the I/O before sanlock, configure
multipath to queue I/O for 192 seconds (8 * sanlock:io_timeout). With
the default polling interval (5 seconds), this means using
`no_path_retry` value of 39.

Here are some possible combinations:

| outage duration             |   50 |   75 |  100 |  125 |
|-----------------------------|------|------|------|------|
| sanlock:io_timeout          |   10 |   15 |   20 |   25 |
| multipath/no_path_retry[1]  |   16 |   24 |   32 |   40 |

[1] Using 5 seconds polling_interval.

See "Sanlock renewal flow" section for more info on the calculation.


## Configuring vdsm

To configure sanlock to use longer I/O timeout, we need to configure
vdsm, since vdsm is managing sanlock.

For each host, install this vdsm configuration drop-in file:

    $ cat /etc/vdsm/vdsm.conf.d/99-FooIO.conf
    # Configuration for FooIO storage.

    [sanlock]
    # Tolarate up to 120 seconds storage outage.
    # (io_timeout = storage outage / 5)
    io_timeout = 24


## Configuring multipath

When using longer `sanlock:io_timeout` in vdsm, we need to update
multipath to use larger `no_path_retry` value.

For each host, install this multipath configuration drop-in file:

    $ cat /etc/multipath/conf.d/FooIO.conf
    # Configuration for FooIO storage.

    overrides {
        # For 24 seconds sanlock:io_timeout.
        # (no_path_retry = sanlock:io_timeout * 8 / polling_interval)
        no_path_retry 39
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

The log shows that we use `sanlock:io_timeout` of 15 seconds.


## Sanlock renewal flow

Here are example flows, showing what happens under the hood during a
storage outage.

These flows use 15 seconds `sanlock:io_timeout`. With this timeout,
sanlock will time out renewal after 15 seconds, and perform a renewal
every 30 seconds.

When storage server fails, multipath detects that all paths to storage
are faulty, and start to queue I/O. When sanlock try to submit I/O, the
requests are queued, so sanlock renewal will time out.

When multipath handles a map with faulty paths, it checks the paths
every 5 seconds (polling interval). Once one path becomes active again,
queued I/O will be submitted again to storage.

Sanlock does not know when a storage server has failed. It calculates
the time since the last successful renewal, and ensures that lease
expires after 8 * `sanlock:io_timeout` seconds since the last renewal.

### Successful recovery from outage - short

In this flow, the outage starts right before sanlock renew the lease, so
sanlock has less time to recover. Storage outage duration is 75 seconds.

```
 00  sanlock renewal succeeds
 30  storage server fails
 30  sanlock try to renew lease
 45  sanlock renwal timeout
 60  sanlock try to renew lease
 75  sanlock renewal timeout
 90  sanlock try to renew lease
105  storage server up again
105  sanlock renwal succeeds
```

### Successful recovery from outage - long

In this flow, the outage starts right after sanlock renew the lease, so
sanlock has more time to recover. Storage outage duration is 105 seconds.

```
 00  sanlock renewal succeeds
 00  storage server fails
 30  sanlock try to renew lease
 45  sanlock renwal timeout
 60  sanlock try to renew lease
 75  sanlock renewal timeout
 90  sanlock try to renew lease
105  storage server up again
105  sanlock renwal succeeds
```

### Failure to renew a lease

In this flow storage was not accessible after the last renewal attempt,
so the lease has expired and sanlock killed the VM. Storage outage
duration is 76 seconds.

```
 00  sanlock renewal succeeds
 30  storage server fails
 30  sanlock try to renew lease
 45  sanlock renwal timeout
 60  sanlock try to renew lease
 75  sanlock renewal timeout
 90  sanlock try to renew lease
105  sanlock renwal timeout
106  storage is up again
120  sanlock expires lease, VM killed
```

## Additional info

- sanlock timeouts
  https://pagure.io/sanlock/raw/master/f/src/timeouts.h

- multipath configuration
  https://linux.die.net/man/5/multipath.conf
