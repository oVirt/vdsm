"""
This is a reproducer for https://bugzilla.redhat.com/1553133, reproducing
the issue of lvm read-only like commands running on non-spm host corrupting
vg metadata.

When running a regular node without the --read-only argument the script will
reproduce the issue on lvm2 < 2.0.3 (e.g RHEL 7.7). With lvm2 >= 2.03, the
script can verify that the issue in lvm was fixed.

When running a regular node with the --read-only option, the script uses
locking_type=4, which should avoid the issue with lvm2 < 2.0.3. The --read-only
argument does nothing on lvm2 >= 2.03.

The script provides 4 sub commands:
- run-manager
- run-regular
- create-vg
- remove-vg
- log-stats

run-manager must run only on one host. run-regular can be run on one or more
hosts.

The run-manager command simulates SPM extend volume flow using threaded TCP
server.  When started, the server wait for client requests.

The run-regular command starts worker threads connecting to the manager server
and preforming storage operations on the manager node:
- create lv
- extend lv
- remove lv

The worker threads perform read-only operations locally:
- activate lv
- refresh lv
- deactivate lv

How to run the test:

1. Create a new vg on iSCSI or FC storage

    # python extend.py create-vg vg-name /dev/mapper/xxx

This creates a pv from /dev/mapper/xxx using vdsm configuration, and then
creates a new vg named vg-name from this pv.

NOTE: To run the test with 50 workers, the pv size must be a least 135G.

You can remove the vg later using:

    # python extend.py remove-vg vg-name /dev/mapper/xxx

If the vg was corrupted, the easiest way to remove it is using wipefs:

    # wipefs -a /dev/mapper/xxx

2. Run this on the manager node:

    # python extend.py run-manager vg-name /dev/mapper/xxx

Make sure port 8000 is accessible on the manager node.

3. Run this on a regular node:

    # python extend.py run-regular manager-host vg-name /dev/mapper/xxx \
        2>run-regular.log

4. To get stats from the regular node log, run:

    # python extend.py log-stats run-regular.log

"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import collections
import datetime
import json
import logging
import os
import random
import re
import signal
import socket
import threading
import time

from contextlib import closing

from six.moves import socketserver

try:
    # builtin subprocess in python 2 can have deadlocks with high concurrency.
    import subprocess32 as subprocess
except ImportError:
    import subprocess

terminated = threading.Event()


class CommandError(Exception):
    msg = ("Command {self.cmd} failed\n"
           "rc={self.rc}\n"
           "out={self.out}\n"
           "err={self.err}")

    def __init__(self, cmd, rc, out, err):
        self.cmd = cmd
        self.rc = rc
        self.out = out
        self.err = err

    def __str__(self):
        return self.msg.format(self=self)


class LVM(object):
    """
    LVM helper, performing lvm operations using the same configuration used by
    vdsm.
    """

    # When running read only commands on remote node, command may fail because
    # vg metadata header was modified on the manager node during the command.
    # There is no way to detect such failure, so the only way to recover is to
    # retry the command.
    #
    # In related bugs we see lvm mdetadata chaged every 5 seconds. Testing
    # using 10 times more load (5 metadata chagnes per second) show that we
    # need up to 3 retries. Use higher value to allow testing higher loads.
    READ_ONLY_RETRIES = 5

    # If a command fails, wait for this amount before retrying.
    RETRY_DELAY = 0.1

    # If retry fails, increase the delay by this factor.
    RETRY_BACKUP_OFF = 2

    # This must match vdsm lvm configuration
    # (vdsm.storage.lvm.LVMCONF_TEMPLATE).
    CONFIG = """
devices {
 preferred_names = ["^/dev/mapper/"]
 ignore_suspended_devices=1
 write_cache_state=0
 disable_after_error_count=3
 filter=%(filter)s
}
global {
 locking_type=%(locking_type)s
 prioritise_write_locks=1
 wait_for_locks=1
 use_lvmetad=0
}
backup {
 retain_min=50
 retain_days=0
}
""".replace("\n", " ")

    # Limit number of concurrent lvm commands, so waiting during a retry
    # loop (holding the semaphore) will lower the load on the manager
    # node, increasing the chance that the next retry will be successful.
    command_sem = threading.BoundedSemaphore(10)

    def __init__(self, options, read_only=False):
        self.vg_name = options.vg_name
        self.pv_names = tuple(options.pv_name)
        self.verbose = options.verbose
        self.read_only = read_only

    def lv_full_name(self, lv_name):
        return self.vg_name + "/" + lv_name

    def create_lv(self, lv_name, size_mb):
        if self.read_only:
            raise RuntimeError("Not allowed in read only mode")

        full_name = self.lv_full_name(lv_name)

        logging.info("creating %s", full_name)

        return self.run(
            "lvcreate",
            "--autobackup", "n",
            "--contiguous", "n",
            "--activate", "n",
            "--size", "%sm" % size_mb,
            "--name", lv_name,
            self.vg_name)

    def extend_lv(self, lv_name, size_mb):
        if self.read_only:
            raise RuntimeError("Not allowed in read only mode")

        full_name = self.lv_full_name(lv_name)

        logging.info("extending %s", full_name)

        return self.run(
            "lvextend",
            "--autobackup", "n",
            "--size", "+%sm" % size_mb,
            full_name)

    def refresh_lv(self, lv_name):
        full_name = self.lv_full_name(lv_name)
        logging.info("refreshing %s", full_name)
        return self.run("lvchange", "--refresh", full_name)

    def activate_lv(self, lv_name):
        # Replicating vdsm behavior, refreshing active lvs.
        # (vdsm.storage.lvm.activateLVs)
        dev_path = os.path.join("/dev", self.vg_name, lv_name)
        if os.path.exists(dev_path):
            return self.refresh_lv(lv_name)
        else:
            full_name = self.lv_full_name(lv_name)
            logging.info("activating %s", full_name)
            return self.run("lvchange", "--available", "y", full_name)

    def deactivate_lv(self, lv_name):
        full_name = self.lv_full_name(lv_name)
        logging.info("deactivating %s", full_name)
        return self.run("lvchange", "--available", "n", full_name)

    def remove_lv(self, lv_name):
        if self.read_only:
            raise RuntimeError("Not allowed in read only mode")

        full_name = self.lv_full_name(lv_name)
        logging.info("removing %s", full_name)
        return self.run("lvremove", "--autobackup", "n", full_name)

    def create_vg(self):
        if self.read_only:
            raise RuntimeError("Not allowed in read only mode")

        logging.info("creating vg %s", self.vg_name)

        extent_size = "128m"

        self.run(
            "pvcreate",
            "--metadatasize", extent_size,
            "--metadatacopies", "2",
            "--metadataignore", "y",
            *self.pv_names)

        self.run(
            "pvchange",
            "--metadataignore", "n",
            self.pv_names[0])

        self.run(
            "vgcreate",
            "--physicalextentsize", extent_size,
            self.vg_name,
            *self.pv_names)

    def remove_vg(self):
        if self.read_only:
            raise RuntimeError("Not allowed in read only mode")

        logging.info("deleting vg %s", self.vg_name)

        self.run(
            "lvchange",
            "--available", "n",
            self.vg_name)

        self.run("vgremove", self.vg_name)

        self.run("pvremove", *self.pv_names)

    def run(self, command, *args):
        config = self.CONFIG % {
            "filter": self.format_filter(),
            "locking_type": "4" if self.read_only else "1"
        }
        cmd = [command, "--config", config]
        if self.verbose:
            cmd.append('-vvvv')
        cmd.extend(args)

        with self.command_sem:
            if self.read_only:
                return self.run_read_only(cmd)
            else:
                return run(cmd)

    def format_filter(self):
        """
        Format lvm filter syntax for self.pv_names:

            [ "a|^/dev/foo$|", "a|^/dev/bar$|", "r|.*|" ]
        """
        items = ['"a|^%s$|"' % pv for pv in self.pv_names]
        items.append('"r|.*|"')
        return "[ %s ]" % ", ".join(items)

    def run_read_only(self, cmd):
        """
        Run read only lvm command, retrying on failures with exponential
        back-off.
        """
        delay = self.RETRY_DELAY

        for i in range(1, self.READ_ONLY_RETRIES + 1):
            try:
                return run(cmd)
            except CommandError as e:
                if i == self.READ_ONLY_RETRIES or e.rc < 0:
                    raise
                logging.warning("Retry %d failed: %s", i, e)
                time.sleep(delay)
                delay *= self.RETRY_BACKUP_OFF


class ManagerClient(object):

    def __init__(self, options):
        self.sock = socket.socket()
        self.sock.connect((options.manager_host, options.manager_port))
        self.r = self.sock.makefile("r")
        self.w = self.sock.makefile("w", 0)

    def close(self):
        self.r.close()
        self.w.close()
        self.sock.close()

    def create(self, lv_name, size_mb):
        logging.info("creating lv %s", lv_name)
        self.call("create", {"lv_name": lv_name, "size_mb": size_mb})

    def extend(self, lv_name, size_mb):
        logging.info("extending lv %s", lv_name)
        self.call("extend", {"lv_name": lv_name, "size_mb": size_mb})

    def remove(self, lv_name):
        logging.info("removing lv %s", lv_name)
        self.call("remove", {"lv_name": lv_name})

    def call(self, command, args):
        msg = {"command": command, "args": args}
        line = json.dumps(msg).encode("utf-8") + b"\n"

        self.w.write(line)
        self.w.flush()

        res = self.r.readline()
        if not res:
            raise RuntimeError("Server closed connection")

        if res != b"\n":
            raise RuntimeError("Unexpected response: %r" % res)


class Manager(socketserver.ThreadingTCPServer):

    daemon_threads = True
    allow_reuse_address = True
    timeout = 1.0

    # Set when creating an instance.
    options = None


class ManagerConnection(socketserver.BaseRequestHandler):

    def handle(self):
        logging.info("connection %s opened", self.client_address)

        lvm = LVM(self.server.options)
        r = self.request.makefile("r")
        w = self.request.makefile("w", 0)
        try:
            while not terminated.is_set():
                line = r.readline()
                if not line:
                    break

                msg = json.loads(line.decode("utf-8"))
                command = msg["command"]
                args = msg["args"]

                if command == "create":
                    lvm.create_lv(args["lv_name"], args["size_mb"])
                elif command == "extend":
                    lvm.extend_lv(args["lv_name"], args["size_mb"])
                elif command == "remove":
                    lvm.remove_lv(args["lv_name"])
                else:
                    raise RuntimeError("Unsupported command: %r" % msg)

                w.write(b"\n")
                w.flush()
        except Exception as e:
            logging.exception("Error processing request: %s", e)
        finally:
            r.close()
            w.close()

        logging.info("connection %s closed", self.client_address)


# Commands


def run_regular(options):
    logging.info("starting %d workers", options.concurrency)

    register_termination_signals()
    workers = []

    for i in range(options.concurrency):
        name = "worker-%03d" % i
        t = threading.Thread(
            target=regular_worker,
            name=name,
            args=(options,))
        t.daemon = True
        t.start()
        workers.append(t)
        time.sleep(0.5)

    logging.info("waiting for workers")

    while workers:
        # Using join with a timeout avoid blocking signal handling.
        workers[0].join(1.0)
        if not workers[0].is_alive():
            workers.pop(0)

    logging.info("workers stopped")


def run_manager(options):
    manager = Manager(("", options.manager_port), ManagerConnection)
    manager.options = options
    logging.info("manager listening on port %d", options.manager_port)

    register_termination_signals()

    while not terminated.is_set():
        manager.handle_request()

    logging.info("manager stopped")


def create_vg(options):
    lvm = LVM(options)
    lvm.create_vg()


def remove_vg(options):
    lvm = LVM(options)
    lvm.remove_vg()


def log_stats(options):
    """
    Print stats from run-regular log.
    """
    # 2019-01-06 20:46:44,210 INFO    (MainThread) starting 50 workers
    log_re = re.compile(
        r"(\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d),\d\d\d ([A-Z]+)\s+\(.+\) (.+)")
    retry_re = re.compile(r"Retry (\d+) failed")
    action_re = re.compile(
        r"(creating|removing|activating|deactivating|extending|refreshing) ")
    read_only = ("activating", "deactivating", "refreshing")
    datestamp_fmt = "%Y-%m-%d %H:%M:%S"

    stats = collections.defaultdict(int)
    start_datetime = None

    with open(options.logfile) as f:
        for line in f:
            m = log_re.match(line)
            if m is None:
                continue  # continuation line.

            datestamp, level, message = m.groups()

            if start_datetime is None:
                start_datetime = datetime.datetime.strptime(
                    datestamp, datestamp_fmt)

            if level == "INFO":
                m = action_re.match(message)
                if m is not None:
                    action = m.group(1)
                    stats[action] += 1
                    if action in read_only:
                        stats["read-only"] += 1
            elif level == "WARNING":
                stats["warnings"] += 1
                m = retry_re.match(message)
                if m is not None:
                    stats["retries"] += 1
                    retry = int(m.group(1))
                    stats["retry %d" % retry] += 1
                    if retry > stats["max-retry"]:
                        stats["max-retry"] = retry
            elif level == "ERROR":
                stats["errors"] += 1

        end_datetime = datetime.datetime.strptime(datestamp, datestamp_fmt)
        total_time = end_datetime - start_datetime

        stats["total-time"] = total_time.seconds
        stats["extend-rate"] = stats["extending"] / stats["total-time"]
        stats["retry-rate"] = stats["retries"] / stats["read-only"]

        print(json.dumps(stats, indent=4, sort_keys=True))


# Helpers


def register_termination_signals():
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)


def terminate(signo, frame):
    logging.info("terminating after signal %d", signo)
    terminated.set()


def regular_worker(options):
    """
    Regular host thread simulator.
    """
    logging.info("starting")

    try:
        lvm = LVM(options, read_only=options.read_only)

        worker_name = threading.current_thread().name
        manager = ManagerClient(options)
        with closing(manager):
            for i in range(options.iterations):
                lv_name = "%s-%06d" % (worker_name, i)

                manager.create(lv_name, options.lv_size_mb)
                lvm.activate_lv(lv_name)
                try:
                    for j in range(20):
                        manager.extend(lv_name, options.lv_size_mb)
                        lvm.refresh_lv(lv_name)

                        # Randomize extend delay for more real behaviour.
                        delay = options.extend_delay * random.random() * 2
                        if terminated.wait(delay):
                            break

                finally:
                    lvm.deactivate_lv(lv_name)
                    manager.remove(lv_name)
                if terminated.is_set():
                    break
    except Exception:
        logging.exception("worker failed")

    logging.info("stopping")


def run(cmd):
    logging.debug("running %s", cmd)

    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    out, err = p.communicate()

    if p.returncode != 0:
        raise CommandError(cmd, p.returncode, out, err)

    if err:
        logging.warning(
            "succeeded with warnings cmd=%r out=%r err=%r",
            cmd, out, err)
    else:
        logging.debug("succeeded out=%r", out)

    return out


def add_common_options(parser):
    # Optional arguments

    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="show debug logs")

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="lvm command verbosity")


def add_vg_options(parser):
    parser.add_argument(
        "vg_name",
        help="volume group name")

    parser.add_argument(
        "pv_name",
        nargs="+",
        help="pv names used by the vg")


def add_manager_options(parser):
    parser.add_argument(
        "-p", "--manager-port",
        type=int,
        default=8000,
        help="manager port")


parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(title="commands")

# run-manager command.

run_manager_parser = subparsers.add_parser(
    "run-manager",
    help="run manager")

add_common_options(run_manager_parser)
add_manager_options(run_manager_parser)
add_vg_options(run_manager_parser)

run_manager_parser.set_defaults(command=run_manager)

# run-regular command.

run_regular_parser = subparsers.add_parser(
    "run-regular",
    help="run regular host flows")

add_common_options(run_regular_parser)
add_manager_options(run_regular_parser)

run_regular_parser.add_argument(
    "-r", "--read-only",
    action="store_true",
    help="Enable read-only mode (default False)")

run_regular_parser.add_argument(
    "-c", "--concurrency",
    type=int,
    default=50,
    help="number of workers (default 50)")

run_regular_parser.add_argument(
    "-n", "--iterations",
    type=int,
    default=10,
    help="number of iteration per worker")

run_regular_parser.add_argument(
    "-e", "--extend-delay",
    type=int,
    default=1,
    help="average time to wait between extends in seconds (default 1)")

run_regular_parser.add_argument(
    "-s", "--lv-size-mb",
    type=int,
    default=128,
    help="lv size in megabytes")

# Required arguments.

run_regular_parser.add_argument(
    "manager_host",
    help="manager node address")

add_vg_options(run_regular_parser)

run_regular_parser.set_defaults(command=run_regular)

# create-vg command

create_vg_parser = subparsers.add_parser(
    "create-vg",
    help="create vg for testing")

add_common_options(create_vg_parser)

add_vg_options(create_vg_parser)

create_vg_parser.set_defaults(command=create_vg)

# remove-vg command

remove_vg_parser = subparsers.add_parser(
    "remove-vg",
    help="remove testing vg")

add_common_options(remove_vg_parser)

add_vg_options(remove_vg_parser)

remove_vg_parser.set_defaults(command=remove_vg)

# log-stats command

log_stats_parser = subparsers.add_parser(
    "log-stats",
    help="show stats from run log")

log_stats_parser.add_argument(
    "logfile",
    help="log file to analyze")

add_common_options(log_stats_parser)

log_stats_parser.set_defaults(command=log_stats)

# Run

options = parser.parse_args()

logging.basicConfig(
    level=logging.DEBUG if options.debug else logging.INFO,
    format="%(asctime)s %(levelname)-7s (%(threadName)s) %(message)s")

options.command(options)
