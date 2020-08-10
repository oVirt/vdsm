# Profile Vdsm execution using Yappi

To profile Vdsm execution using Yappi, take the following steps:

## Install python3-yappi on profiled el8 host machine
```
sudo dnf install \
https://trunk.rdoproject.org/rhel8-master/deps/latest/Packages/python3-yappi-1.0-2.el8ost.x86_64.rpm
```

## Stop Vdsm
```
sudo systemctl stop vsdmd
```

## Edit /etc/vdsm/vdsm.conf
```
[devel]

# Enable whole process profiling (requires yappi profiler).
cpu_profile_enable = true

# Profile file name (/run/vdsm/vdsmd.prof)
cpu_profile_filename = /run/vdsm/vdsmd.prof

# Profile file format (pstat, callgrind, ystat)
cpu_profile_format = pstat

# Profile builtin functions used by standard Python modules. false by
# default.
# cpu_profile_builtins = true

# Sets the underlying clock type (cpu, wall)
cpu_profile_clock = wall
```

## Start Vdsm
```
sudo systemctl start vdsmd
```

## Stop Vdsm upon completion of profiled operation
```
sudo systemctl stop vdsmd
```

## View dumped profile

### profile-stats

The common way is to use pstat dumps and view them as text-based reports using `profile-stats`:
```
$ contrib/profile-stats  -r 5  -s cumtime  -c  vdsmd.prof

Wed Jan 22 10:36:51 2020    /tmp/vdsmd.prof

         17622945 function calls (17686390 primitive calls) in 70.814 seconds

   Ordered by: cumulative time
   List reduced from 2741 to 5 due to restriction <5>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
16320/16364  0.476    0.000 17540.944   1.072 threading.py:263(Condition.wait)
7438/7460    0.153    0.000 8995.480    1.206 queue.py:147(Queue.get)
2725/2745    0.011    0.000 8178.056    2.979 threadPool.py:136(WorkerThread._processNextTask)
2725/2745    0.031    0.000 8177.181    2.979 threadPool.py:64(ThreadPool.getNextTask)
2576/2592    0.121    0.000 6583.191    2.540 executor.py:309(_Worker._execute_task)


   Ordered by: cumulative time
   List reduced from 2741 to 5 due to restriction <5>

Function                           was called by...
                                   ncalls  tottime  cumtime
threading.py:263(Condition.wait)   <- 2587/2570    0.067 6484.476  executor.py:454(TaskQueue.get)
                                   5140/5118    0.117 8995.213  queue.py:147(Queue.get)
                                   2831/2830    0.073  421.066  schedule.py:148(Scheduler._loop)
                                   3    0.000    2.549  sdc.py:118(StorageDomainCache._realProduce)
                                   288/285    0.009 1222.286  threading.py:533(Event.wait)
...
```
Above example shows function calls sorted by cumulative execution time, in compact report,
limited to first 5 rows.

See `contrib/profile-stats -h` and `contrib/profile-stats` docstring for further usage information.

### kCachegrind

For a GUI view you can dump profile in a callgrind format by setting `cpu_profile_format = callgrind`
in `/etc/vdsm/vdsm.conf` and use kCachgrind to view it with call graphs, installed by running:
```
sudo dnf install kcachegrind graphviz
```

## Notes

- Remove `/run/vdsm/vdsmd.prof` between Vdsm restarts.
- Comment back the cpu profile options in `/etc/vdsm/vdsm.conf` and restart Vdsm when done.
