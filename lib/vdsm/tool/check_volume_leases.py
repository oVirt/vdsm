# Copyright 2018 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from collections import defaultdict
import argparse

import sanlock

from vdsm import client
from vdsm import utils
from vdsm.config import config
from . import UsageError
from . import common
from . import expose

_NAME = "check-volume-leases"


@expose(_NAME)
def main(*args):
    """
    This tool is used to check and optionally repair broken volume leases.
    """
    parsed_args = _parse_args(args)

    if not parsed_args.repair and not _confirm_check_leases():
        return

    cli = client.connect(parsed_args.host, parsed_args.port,
                         use_tls=parsed_args.use_ssl)
    with utils.closing(cli):
        print()
        print("Checking active storage domains. This can take several "
              "minutes, please wait.")
        broken_leases = _get_leases_to_repair(cli)
        if not broken_leases:
            print()
            print("There are no leases to repair.")
            return

    print()
    _print_broken_leases(broken_leases)
    if not parsed_args.repair and not _confirm_repair_leases():
        return

    _repair(broken_leases)


def _confirm_check_leases():
    return common.confirm("""\
WARNING: Make sure there are no running storage operations.

Do you want to check volume leases? [yes,NO] """)


def _confirm_repair_leases():
    return common.confirm("Do you want to repair the leases? [yes,NO] ")


def _parse_args(args):
    parser = argparse.ArgumentParser("This tool is used to check volume "
                                     "lease, and to optionally repair the "
                                     "ones that need to be repaired")
    parser.add_argument('--repair', help="repair broken volume leases",
                        action="store_true")
    parser.add_argument('-u', '--unsecured', action='store_false',
                        dest='use_ssl', default=True,
                        help="use unsecured connection")
    parser.add_argument('-H', '--host', default='localhost')
    parser.add_argument(
        '-p', '--port', default=config.getint('addresses', 'management_port'))

    return parser.parse_args(args=args[1:])


def _get_leases_to_repair(cli):
    # Returns the following structure:
    # {
    #    "domain-uuid": {
    #      "img-uuid: {
    #        "vol-uuid": {
    #          "path": "/rhev/data-center/..."
    #          "offset": 0
    #       }
    #   }
    # }
    pools = cli.Host.getConnectedStoragePools()
    if not pools:
        print()
        raise UsageError(
            "The storage pool is not connected.\n"
            "Please make sure the host is active before running the tool."
        )

    sp_uuid, = pools
    broken_leases = {}
    for sd_uuid in cli.Host.getStorageDomains(storagepoolID=sp_uuid):
        sd_broken_leases = _get_domain_broken_leases(cli, sp_uuid, sd_uuid)
        if sd_broken_leases:
            broken_leases[sd_uuid] = sd_broken_leases

    return broken_leases


def _get_domain_broken_leases(cli, sp_uuid, sd_uuid):
    broken_leases = defaultdict(dict)
    for img_uuid in cli.StorageDomain.getImages(storagedomainID=sd_uuid):
        vols_uuids = cli.StorageDomain.getVolumes(storagepoolID=sp_uuid,
                                                  storagedomainID=sd_uuid,
                                                  imageID=img_uuid)
        for vol_uuid in vols_uuids:
            try:
                info = cli.Volume.getInfo(storagepoolID=sp_uuid,
                                          storagedomainID=sd_uuid,
                                          imageID=img_uuid,
                                          volumeID=vol_uuid)
            except client.Error as e:
                print()
                print("Error: failed to get volume info: {} (domain: {}, "
                      "image: {}, volume: {}"
                      .format(e, sd_uuid, img_uuid, vol_uuid))
                continue

            if 'lease' not in info:
                # This domain does not support leases.
                return {}

            leaseinfo = info['lease']
            if 'owners' not in leaseinfo:
                # The lease is broken
                broken_leases[img_uuid][vol_uuid] = leaseinfo

    # Convert the defaultdict to the standard dict in order to get an error
    # when getting a key that doesn't exist, rather than creating an empty
    # dict.
    return dict(broken_leases)


def _print_broken_leases(broken_leases):
    print("The following volume leases need repair:")
    print()

    for sd_uuid in broken_leases:
        print("- domain: {}".format(sd_uuid))
        print()
        for img_uuid in broken_leases[sd_uuid]:
            print("  - image: {}".format(img_uuid))
            for vol_uuid in broken_leases[sd_uuid][img_uuid]:
                print("    - volume: {}".format(vol_uuid))
        print()


def _repair(broken_leases):
    print("Repairing volume leases ...")
    total = 0
    repaired = 0
    for sd_uuid in broken_leases:
        for img_uuid in broken_leases[sd_uuid]:
            for vol_uuid in broken_leases[sd_uuid][img_uuid]:
                total += 1
                vol_lease = broken_leases[sd_uuid][img_uuid][vol_uuid]
                try:
                    sanlock.write_resource(
                        sd_uuid,
                        vol_uuid,
                        [(vol_lease['path'], vol_lease['offset'])])
                    repaired += 1
                except sanlock.SanlockException as e:
                    print("Failed to repair lease of volume {}/{}. Error {}"
                          .format(vol_lease['image'], vol_lease['volume'], e))

    print("Repaired ({}/{}) volume leases.".format(repaired, total))
