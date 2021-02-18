# Copyright 2015-2016 Red Hat, Inc.
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
import json
import sqlite3
import sys

from . import expose

from vdsm import client
from vdsm.config import config
from vdsm import utils

# BLANK_UUID is re-declared here since it cannot be imported properly. This
# constant should be introduced under lib publicly available
_BLANK_UUID = '00000000-0000-0000-0000-000000000000'
_NAME = 'dump-volume-chains'
UNKNOWN_IMAGE = "unknown-image"
UNKNOWN_PARENT = "unknown-parent"


class DumpChainsError(Exception):
    pass


class NoConnectedStoragePoolError(DumpChainsError):
    pass


class ChainError(DumpChainsError):
    def __init__(self, volumes_children):
        self.volumes_children = volumes_children


class DuplicateParentError(ChainError):
    description = ("more than one volume pointing to the same parent volume "
                   "e.g: (_BLANK_UUID<-a), (a<-b), (a<-c)")


class UnknownParentError(ChainError):
    description = ("there are volumes in the chain missing the parent info "
                   "in their metadata, please check the metadata integrity")


class UnknownImageError(ChainError):
    description = ("there are volumes in the storage missing the image info "
                   "in their metadata, these are listed here")


class NoBaseVolume(ChainError):
    description = ("no volume with a parent volume Id _BLANK_UUID found e.g: "
                   "(a<-b), (b<-c)")


class ChainLoopError(ChainError):
    description = ("a loop found in the volume chain. This happens if a "
                   "volume points to one of it's parent volume e.g.: "
                   "(BLANK_UUID<-a), (a<-b), (b<-c), (c<-a)")


class OrphanVolumes(ChainError):
    description = ("there are volumes that are part of an image and are "
                   "pointing to volumes which are not part of the chain e.g: "
                   "(BLANK_UUID<-a), (a<-b), (c<-d)")


@expose(_NAME)
def dump_chains(*args):
    """
    dump-volume-chains
    Query VDSM about the existing structure of image volumes and prints
    them in an ordered fashion with optional additional info per volume.
    Alternatively, dumps the volumes information in json format without
    analysis.
    """
    parsed_args = _parse_args(args)
    cli = client.connect(parsed_args.host, parsed_args.port,
                         use_tls=parsed_args.use_ssl)
    with utils.closing(cli):
        volumes_info = _get_volumes_info(cli, parsed_args.sd_uuid)
        if parsed_args.output == 'text':
            # perform analysis and print in human readable format
            image_chains = _get_volumes_chains(volumes_info)
            _print_volume_chains(image_chains, volumes_info)
        elif parsed_args.output == 'json':
            # no analysis, dump chains in json format
            json.dump(volumes_info, sys.stdout, indent=2)
        elif parsed_args.output == 'sqlite':
            # no analysis, dump chains in sql format
            _dump_sql(volumes_info, parsed_args.sqlite_file)
        else:
            raise ValueError('unknown output format %s' % parsed_args.output)


def _parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('sd_uuid', help="storage domain UUID")
    parser.add_argument('-u', '--unsecured', action='store_false',
                        dest='use_ssl', default=True,
                        help="use unsecured connection")
    parser.add_argument('-H', '--host', default='localhost')
    parser.add_argument('-o', '--output', choices=['text', 'json', 'sqlite'],
                        default='text', help="select output format")
    parser.add_argument(
        '-p', '--port', default=config.getint('addresses', 'management_port'))
    parser.add_argument('-f', '--sqlite-file', help="sqlite3 db output file")

    parsed_args = parser.parse_args(args=args[1:])

    if parsed_args.output == 'sqlite' and parsed_args.sqlite_file is None:
        parser.error("--output sqlite requires --sqlite-file.")

    return parsed_args


def _dump_sql(volumes_info, sql_file):
    with sqlite3.connect(sql_file) as con:
        con.executescript("""
            DROP TABLE IF EXISTS volumes;
            CREATE TABLE volumes(
                uuid UUID,
                parent UUID,
                image UUID,
                status TEXT,
                voltype TEXT,
                format TEXT,
                legality TEXT,
                type TEXT,
                disktype TEXT,
                capacity UNSIGNED INTEGER,
                apparentsize UNSIGNED INTEGER,
                truesize UNSIGNED INTEGER,
                ctime UNSIGNED INTEGER
            );
            """)
        con.executemany("""
            INSERT INTO volumes VALUES (
                :uuid,
                :parent,
                :image,
                :status,
                :voltype,
                :format,
                :legality,
                :type,
                :disktype,
                :capacity,
                :apparentsize,
                :truesize,
                :ctime
            );
            """, _iter_volumes_info(volumes_info))


def _iter_volumes_info(volumes_info):
    for _, img_volumes in volumes_info.items():
        for _, vol_info in img_volumes.items():
            yield vol_info


def _get_volumes_info(cli, sd_uuid):
    volumes_info = defaultdict(dict)

    volumes = cli.StorageDomain.dump(sd_id=sd_uuid)["volumes"]

    # find volumes per image
    for vol_id, vol_info in volumes.items():
        image_id = vol_info.get("image", UNKNOWN_IMAGE)
        # normalize parent info
        parent = vol_info.get("parent", UNKNOWN_PARENT)
        vol_info["parent"] = parent
        volumes_info[image_id][vol_id] = vol_info

    # add template volumes
    for img_volumes in volumes_info.values():
        for vol in list(img_volumes.values()):
            parent_id = vol["parent"]
            if parent_id not in img_volumes and parent_id in volumes:
                img_volumes[parent_id] = volumes[parent_id]

    return volumes_info


def _get_volumes_chains(volumes_info):
    image_chains = {}

    for img_uuid, volumes in volumes_info.items():

        # to avoid 'double parent' bug here we don't use a dictionary
        volumes_children = []  # [(parent_vol_uuid, child_vol_uuid),]
        for vol_uuid, vol_info in volumes.items():
            volumes_children.append((vol_info['parent'], vol_uuid))

        if img_uuid == UNKNOWN_IMAGE:
            # do not build chain of volumes with unknown image
            image_chains[img_uuid] = UnknownImageError(volumes_children)
        elif any(UNKNOWN_PARENT in volume for volume in volumes_children):
            # do not build chain if any volume has unknown parent
            image_chains[img_uuid] = UnknownParentError(volumes_children)
        else:
            try:
                image_chains[img_uuid] = _build_volume_chain(volumes_children)
            except ChainError as e:
                image_chains[img_uuid] = e

    return image_chains


def _build_volume_chain(volumes_children):
    volumes_by_parents = dict(volumes_children)
    if len(volumes_by_parents) < len(volumes_children):
        raise DuplicateParentError(volumes_children)

    child_vol = _BLANK_UUID
    chain = []  # ordered vol_UUIDs
    while True:
        child_vol = volumes_by_parents.get(child_vol)
        if child_vol is None:
            break  # end of chain
        if child_vol in chain:
            raise ChainLoopError(volumes_children)
        chain.append(child_vol)

    if not chain and volumes_by_parents:
        raise NoBaseVolume(volumes_children)

    if len(chain) < len(volumes_by_parents):
        raise OrphanVolumes(volumes_children)

    return chain


def _print_volume_chains(image_chains, volumes_info):
    if not image_chains:
        print()
        _print_line("(no images found)")
        print()
        return
    print()
    print('Images volume chains (base volume first)')
    for img_uuid, vol_chain in image_chains.items():
        img_volumes_info = volumes_info[img_uuid]
        print()
        _print_line(img_uuid, 'image:')
        print()
        if isinstance(vol_chain, ChainError):
            chain_err = vol_chain
            _print_error(chain_err.description)
            print()
            _print_line('Unordered volumes and children:')
            print()
            for parent, child in chain_err.volumes_children:
                _print_line('- %s <- %s' % (parent, child))
                _print_vol_info(img_volumes_info[child])
                print()
        else:
            for vol in vol_chain:
                _print_line('- ' + vol)
                _print_vol_info(img_volumes_info[vol])
                print()


def _print_vol_info(volume_info):
    robust_volume_info = defaultdict(lambda: '(missing)', volume_info)
    info_fmt = "status: {d[status]}, voltype: {d[voltype]}, " \
               "format: {d[format]}, legality: {d[legality]}, " \
               "type: {d[type]}, capacity: {d[capacity]}, " \
               "truesize: {d[truesize]}"
    formatted_info = info_fmt.format(d=robust_volume_info)
    _print_line('  ' + formatted_info)


def _print_line(body, title=''):
    print('{0:^13}{1}'.format(title, body))


def _print_error(body, title=''):
    print('{0:^13}Error: {1}'.format(title, body))
