#!/usr/bin/python3
"""
iscsiadm initiator test.
Allows testing initiator setup and logins to iSCSI targets via multiple
interfaces.

Requirements:

- Run as root.
- python 3.
- Existing iSCSI target to initiate login to.

Testing 2 iscsi targets with 2 portals each (4 connections in total):

- Login all nodes at once:

    $ sudo python3 initiator.py -i 10.35.18.139 10.35.18.150

    2020-07-26 19:13:50,353 INFO    (MainThread) Removing prior sessions and nodes
    2020-07-26 19:13:50,489 INFO    (MainThread) Deleting all nodes
    2020-07-26 19:13:50,503 INFO    (MainThread) No active sessions
    2020-07-26 19:13:50,607 INFO    (MainThread) Discovered connections: [('iqn.2003-01.org.vm-18-139.iqn2', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn1', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn2', '10.35.18.150:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn1', '10.35.18.150:3260,1')]
    2020-07-26 19:13:50,607 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:13:50,619 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:13:50,632 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.150:3260,1
    2020-07-26 19:13:50,646 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.150:3260,1
    2020-07-26 19:13:50,661 INFO    (MainThread) Login to all nodes
    2020-07-26 19:13:52,691 INFO    (MainThread) Connecting completed in 2.084s
    2020-07-26 19:13:52,725 INFO    (MainThread) Active sessions: tcp: [16785] 10.35.18.150:3260,1 iqn.2003-01.org.vm-18-139.iqn2 (non-flash)
    tcp: [16786] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn2 (non-flash)
    tcp: [16787] 10.35.18.150:3260,1 iqn.2003-01.org.vm-18-139.iqn1 (non-flash)
    tcp: [16788] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn1 (non-flash)

- Login to all nodes at once when a single network interface is down
  spends 120 seconds in total for all connections (2 connections are down):

    $ sudo python3 initiator.py -i 10.35.18.139 10.35.18.150 -d 10.35.18.150

    2020-07-26 19:13:52,821 INFO    (MainThread) Removing prior sessions and nodes
    2020-07-26 19:13:53,561 INFO    (MainThread) Deleting all nodes
    2020-07-26 19:13:53,591 INFO    (MainThread) No active sessions
    2020-07-26 19:13:53,742 INFO    (MainThread) Setting 10.35.18.150 as invalid address for target iqn.2003-01.org.vm-18-139.iqn2
    2020-07-26 19:13:53,743 INFO    (MainThread) Setting 10.35.18.150 as invalid address for target iqn.2003-01.org.vm-18-139.iqn1
    2020-07-26 19:13:53,743 INFO    (MainThread) Discovered connections: [('iqn.2003-01.org.vm-18-139.iqn2', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn1', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn2', '0.0.0.0:0,0'), ('iqn.2003-01.org.vm-18-139.iqn1', '0.0.0.0:0,0')]
    2020-07-26 19:13:53,743 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:13:53,769 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:13:53,798 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 0.0.0.0:0,0
    2020-07-26 19:13:53,832 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 0.0.0.0:0,0
    2020-07-26 19:13:53,868 INFO    (MainThread) Login to all nodes
    2020-07-26 19:15:53,933 ERROR   (MainThread) Some login failed: Command ['iscsiadm', '--mode', 'node', '--loginall=manual'] failed rc=8 out='Logging in to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn2, portal: 0.0.0.0,0] (multiple)\nLogging in to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn2, portal: 10.35.18.139,3260] (multiple)\nLogging in to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn1, portal: 0.0.0.0,0] (multiple)\nLogging in to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn1, portal: 10.35.18.139,3260] (multiple)\nLogin to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn2, portal: 10.35.18.139,3260] successful.\nLogin to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn1, portal: 10.35.18.139,3260] successful.' err='iscsiadm: Could not login to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn2, portal: 0.0.0.0,0].\niscsiadm: initiator reported error (8 - connection timed out)\niscsiadm: Could not login to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn1, portal: 0.0.0.0,0].\niscsiadm: initiator reported error (8 - connection timed out)\niscsiadm: Could not log into all portals'
    2020-07-26 19:15:53,933 INFO    (MainThread) Connecting completed in 120.190s
    2020-07-26 19:15:53,940 INFO    (MainThread) Active sessions: tcp: [16789] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn2 (non-flash)
    tcp: [16790] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn1 (non-flash)

- Perform a single node login at a time (current vdsm way):

    $ sudo python3 initiator.py -i 10.35.18.139 10.35.18.150 -j 1

    2020-07-26 19:15:54,073 INFO    (MainThread) Removing prior sessions and nodes
    2020-07-26 19:15:54,231 INFO    (MainThread) Deleting all nodes
    2020-07-26 19:15:54,243 INFO    (MainThread) No active sessions
    2020-07-26 19:15:54,349 INFO    (MainThread) Discovered connections: [('iqn.2003-01.org.vm-18-139.iqn2', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn1', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn2', '10.35.18.150:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn1', '10.35.18.150:3260,1')]
    2020-07-26 19:15:54,350 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:15:54,359 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:15:54,370 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.150:3260,1
    2020-07-26 19:15:54,384 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.150:3260,1
    2020-07-26 19:15:54,399 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:15:56,213 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:15:58,351 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.150:3260,1
    2020-07-26 19:16:00,599 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.150:3260,1
    2020-07-26 19:16:02,771 INFO    (MainThread) Connecting completed in 8.421s
    2020-07-26 19:16:02,777 INFO    (MainThread) Active sessions: tcp: [16791] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn2 (non-flash)
    tcp: [16792] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn1 (non-flash)
    tcp: [16793] 10.35.18.150:3260,1 iqn.2003-01.org.vm-18-139.iqn2 (non-flash)
    tcp: [16794] 10.35.18.150:3260,1 iqn.2003-01.org.vm-18-139.iqn1 (non-flash)

- Single node login at a time when a single network interface is down
  spends 240 seconds (120 seconds timeout per a failed login) for
  entire logins:

    $ sudo python3 initiator.py -i 10.35.18.139 10.35.18.150 -j 1 -d 10.35.18.150

    2020-07-26 19:16:02,859 INFO    (MainThread) Removing prior sessions and nodes
    2020-07-26 19:16:03,393 INFO    (MainThread) Deleting all nodes
    2020-07-26 19:16:03,400 INFO    (MainThread) No active sessions
    2020-07-26 19:16:03,495 INFO    (MainThread) Setting 10.35.18.150 as invalid address for target iqn.2003-01.org.vm-18-139.iqn2
    2020-07-26 19:16:03,495 INFO    (MainThread) Setting 10.35.18.150 as invalid address for target iqn.2003-01.org.vm-18-139.iqn1
    2020-07-26 19:16:03,495 INFO    (MainThread) Discovered connections: [('iqn.2003-01.org.vm-18-139.iqn2', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn1', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn2', '0.0.0.0:0,0'), ('iqn.2003-01.org.vm-18-139.iqn1', '0.0.0.0:0,0')]
    2020-07-26 19:16:03,495 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:16:03,502 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:16:03,509 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 0.0.0.0:0,0
    2020-07-26 19:16:03,517 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 0.0.0.0:0,0
    2020-07-26 19:16:03,525 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:16:05,194 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:16:07,231 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn2 portal 0.0.0.0:0,0
    2020-07-26 19:18:07,245 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn1 portal 0.0.0.0:0,0
    2020-07-26 19:18:07,245 ERROR   (MainThread) Job failed: Command ['iscsiadm', '--mode', 'node', '--targetname', 'iqn.2003-01.org.vm-18-139.iqn2', '--interface', 'default', '--portal', '0.0.0.0:0,0', '--login'] failed rc=8 out='Logging in to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn2, portal: 0.0.0.0,0] (multiple)' err='iscsiadm: Could not login to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn2, portal: 0.0.0.0,0].\niscsiadm: initiator reported error (8 - connection timed out)\niscsiadm: Could not log into all portals'
    2020-07-26 19:20:07,256 ERROR   (MainThread) Job failed: Command ['iscsiadm', '--mode', 'node', '--targetname', 'iqn.2003-01.org.vm-18-139.iqn1', '--interface', 'default', '--portal', '0.0.0.0:0,0', '--login'] failed rc=8 out='Logging in to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn1, portal: 0.0.0.0,0] (multiple)' err='iscsiadm: Could not login to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn1, portal: 0.0.0.0,0].\niscsiadm: initiator reported error (8 - connection timed out)\niscsiadm: Could not log into all portals'
    2020-07-26 19:20:07,257 INFO    (MainThread) Connecting completed in 243.761s
    2020-07-26 19:20:07,262 INFO    (MainThread) Active sessions: tcp: [16795] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn2 (non-flash)
    tcp: [16796] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn1 (non-flash)

- Perform each connection login in a concurrent thread

    $ sudo python3 initiator.py -i 10.35.18.139 10.35.18.150 -j 4

    2020-07-26 19:20:07,342 INFO    (MainThread) Removing prior sessions and nodes
    2020-07-26 19:20:07,466 INFO    (MainThread) Deleting all nodes
    2020-07-26 19:20:07,473 INFO    (MainThread) No active sessions
    2020-07-26 19:20:07,565 INFO    (MainThread) Discovered connections: [('iqn.2003-01.org.vm-18-139.iqn2', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn1', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn2', '10.35.18.150:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn1', '10.35.18.150:3260,1')]
    2020-07-26 19:20:07,565 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:20:07,571 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:20:07,578 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.150:3260,1
    2020-07-26 19:20:07,585 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.150:3260,1
    2020-07-26 19:20:07,594 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:20:07,594 INFO    (login_1) Login to target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:20:07,594 INFO    (login_2) Login to target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.150:3260,1
    2020-07-26 19:20:07,596 INFO    (login_3) Login to target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.150:3260,1
    2020-07-26 19:20:09,654 INFO    (MainThread) Connecting completed in 2.089s
    2020-07-26 19:20:09,660 INFO    (MainThread) Active sessions: tcp: [16797] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn1 (non-flash)
    tcp: [16798] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn2 (non-flash)
    tcp: [16799] 10.35.18.150:3260,1 iqn.2003-01.org.vm-18-139.iqn2 (non-flash)
    tcp: [16800] 10.35.18.150:3260,1 iqn.2003-01.org.vm-18-139.iqn1 (non-flash)

- Performing each connection login in a concurrent thread, spending
  120 seconds in total for all connections when a network interface
  is down (2 connections are down).

    $ sudo python3 initiator.py -i 10.35.18.139 10.35.18.150 -j 4 -d 10.35.18.150

    2020-07-26 19:20:09,730 INFO    (MainThread) Removing prior sessions and nodes
    2020-07-26 19:20:10,443 INFO    (MainThread) Deleting all nodes
    2020-07-26 19:20:10,451 INFO    (MainThread) No active sessions
    2020-07-26 19:20:10,543 INFO    (MainThread) Setting 10.35.18.150 as invalid address for target iqn.2003-01.org.vm-18-139.iqn2
    2020-07-26 19:20:10,544 INFO    (MainThread) Setting 10.35.18.150 as invalid address for target iqn.2003-01.org.vm-18-139.iqn1
    2020-07-26 19:20:10,544 INFO    (MainThread) Discovered connections: [('iqn.2003-01.org.vm-18-139.iqn2', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn1', '10.35.18.139:3260,1'), ('iqn.2003-01.org.vm-18-139.iqn2', '0.0.0.0:0,0'), ('iqn.2003-01.org.vm-18-139.iqn1', '0.0.0.0:0,0')]
    2020-07-26 19:20:10,544 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:20:10,550 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:20:10,558 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn2 portal 0.0.0.0:0,0
    2020-07-26 19:20:10,566 INFO    (MainThread) Adding node for target iqn.2003-01.org.vm-18-139.iqn1 portal 0.0.0.0:0,0
    2020-07-26 19:20:10,576 INFO    (login_0) Login to target iqn.2003-01.org.vm-18-139.iqn2 portal 10.35.18.139:3260,1
    2020-07-26 19:20:10,577 INFO    (login_1) Login to target iqn.2003-01.org.vm-18-139.iqn1 portal 10.35.18.139:3260,1
    2020-07-26 19:20:10,578 INFO    (login_2) Login to target iqn.2003-01.org.vm-18-139.iqn2 portal 0.0.0.0:0,0
    2020-07-26 19:20:10,578 INFO    (login_3) Login to target iqn.2003-01.org.vm-18-139.iqn1 portal 0.0.0.0:0,0
    2020-07-26 19:22:10,609 ERROR   (MainThread) Job failed: Command ['iscsiadm', '--mode', 'node', '--targetname', 'iqn.2003-01.org.vm-18-139.iqn2', '--interface', 'default', '--portal', '0.0.0.0:0,0', '--login'] failed rc=8 out='Logging in to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn2, portal: 0.0.0.0,0] (multiple)' err='iscsiadm: Could not login to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn2, portal: 0.0.0.0,0].\niscsiadm: initiator reported error (8 - connection timed out)\niscsiadm: Could not log into all portals'
    2020-07-26 19:22:10,609 ERROR   (MainThread) Job failed: Command ['iscsiadm', '--mode', 'node', '--targetname', 'iqn.2003-01.org.vm-18-139.iqn1', '--interface', 'default', '--portal', '0.0.0.0:0,0', '--login'] failed rc=8 out='Logging in to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn1, portal: 0.0.0.0,0] (multiple)' err='iscsiadm: Could not login to [iface: default, target: iqn.2003-01.org.vm-18-139.iqn1, portal: 0.0.0.0,0].\niscsiadm: initiator reported error (8 - connection timed out)\niscsiadm: Could not log into all portals'
    2020-07-26 19:22:10,610 INFO    (MainThread) Connecting completed in 120.067s
    2020-07-26 19:22:10,616 INFO    (MainThread) Active sessions: tcp: [16801] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn2 (non-flash)
    tcp: [16802] 10.35.18.139:3260,1 iqn.2003-01.org.vm-18-139.iqn1 (non-flash)

"""  # NOQA: E501 (comment line too long)

import argparse
import ipaddress
import logging
import subprocess
import time

from concurrent.futures import ThreadPoolExecutor

INVALID_PORTAL = "0.0.0.0:0,0"


class Error(Exception):

    def __init__(self, cmd, rc, out, err):
        self.cmd = cmd
        self.rc = rc
        self.out = out
        self.err = err

    def __str__(self):
        return (
            "Command {self.cmd} failed rc={self.rc} out={self.out!r} "
            "err={self.err!r}"
        ).format(self=self)


def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s (%(threadName)s) %(message)s")

    cleanup()

    connections = discover_targets(args)
    logging.info("Discovered connections: %s", connections)

    start = time.monotonic()

    for target, portal in connections:
        new_node(target, portal)

    if args.concurrency:
        login_threads(connections, args.concurrency)
    else:
        login_all()

    logging.info("Connecting completed in %.3fs",
                 time.monotonic() - start)

    list_sessions()


def run(args):
    logging.debug("Running command %s", args)

    start = time.monotonic()
    p = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    out, err = p.communicate()

    out = out.decode("utf-8").strip()
    err = err.decode("utf-8").strip()

    logging.debug("Command completed in %.3fs: rc=%s out=%r err=%r",
                  time.monotonic() - start, p.returncode, out, err)

    if p.returncode != 0:
        raise Error(args, p.returncode, out, err)

    return out


def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument(
        "-i",
        dest='interfaces',
        nargs='+',
        type=ipaddress.ip_address,
        help="Target interfaces ip addresses",
        required=True)

    p.add_argument(
        "-d",
        dest='disconnected',
        nargs='+',
        default=[],
        type=ipaddress.ip_address,
        help="Target interfaces disconnected for login")

    p.add_argument(
        "-p",
        dest='port',
        type=int,
        default=3260,
        help="Target port (default is 3260)")

    p.add_argument(
        "-j",
        dest='concurrency',
        type=int,
        default=0,
        help="Run login per connection at set concurrency (default is none)")

    p.add_argument(
        "--debug",
        action="store_true",
        help="Show debug logs (default is false)")

    return p.parse_args()


def ip_port(address, port):
    return str(address) + ':' + str(port)


def discover_targets(args):
    connections = []

    for iface in args.interfaces:
        portal = ip_port(iface, args.port)
        out = run([
            "iscsiadm",
            "--mode", "discoverydb",
            "--type", "sendtargets",
            "--interface", "default",
            "--portal", portal,
            "--discover"])

        discovery_delete(portal)

        for line in out.splitlines():
            portal, target = line.split()

            address = ipaddress.ip_address(portal.split(":")[0])
            if address in args.disconnected:
                logging.info("Setting %s as invalid address for target %s",
                             address, target)
                portal = INVALID_PORTAL

            connections.append((target, portal))

    return connections


def discovery_delete(portal):
    run([
        "iscsiadm",
        "--mode", "discoverydb",
        "--type", "sendtargets",
        "--interface", "default",
        "--portal", portal,
        "--op=delete"])


def login_threads(connections, concurrency):
    with ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="login") as executor:
        jobs = [executor.submit(login, target, portal)
                for target, portal in connections]
        for job in jobs:
            try:
                job.result()
            except Exception as e:
                logging.error("Job failed: %s", e)


def login_all():
    logging.info("Login to all nodes")
    try:
        run(["iscsiadm", "--mode", "node", "--loginall=manual"])
    except Error as e:
        # Expected timeout error when there are disconnected portals.
        if e.rc != 8:
            raise
        logging.error("Some login failed: %s", e)


def cleanup():
    logging.info("Removing prior sessions and nodes")
    logout()
    delete_nodes()
    list_sessions()


def new_node(target, portal):
    logging.info("Adding node for target %s portal %s", target, portal)

    run([
        "iscsiadm",
        "--mode", "node",
        "--targetname", target,
        "--interface", "default",
        "--portal", portal,
        "--op=new"])

    run([
        "iscsiadm",
        "--mode", "node",
        "--targetname", target,
        "--interface", "default",
        "--portal", portal,
        "--op=update",
        "--name", "node.startup",
        "--value", "manual"])


def login(target, portal):
    logging.info("Login to target %s portal %s", target, portal)
    run([
        "iscsiadm",
        "--mode", "node",
        "--targetname", target,
        "--interface", "default",
        "--portal", portal,
        "--login"])


def logout():
    try:
        run(["iscsiadm", "--mode", "node", "--logoutall=manual"])
    except Error as e:
        # Expected failure when there are no logged in sessions.
        if e.rc != 21:
            raise


def delete_nodes():
    logging.info("Deleting all nodes")
    try:
        run(["iscsiadm", "--mode", "node", "--op=delete"])
    except Error as e:
        # Expected failure when there is no node.
        if e.rc != 21:
            raise


def list_sessions():
    try:
        out = run(["iscsiadm", "--mode", "session"])
        logging.info("Active sessions: %s", out)
    except Error as e:
        # Expected failure when there are no sessions.
        if e.rc != 21:
            raise
        logging.info("No active sessions")


if __name__ == '__main__':
    main()
