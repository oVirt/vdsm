# Copyright 2015 Red Hat, Inc.
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
from __future__ import print_function
from collections import defaultdict
import errno
import argparse
import socket

from . import expose

from vdsm import vdscli

# BLANK_UUID is re-declared here since it cannot be imported properly. this
# constant should be introduced under lib publically available
_BLANK_UUID = '00000000-0000-0000-0000-000000000000'
_NAME = 'dump-volume-chains'


class DumpChainsError(Exception):
    pass


class ServerError(DumpChainsError):
    def __init__(self, server_result):
        self.code = server_result['status']['code']
        self.message = server_result['status']['message']

    def __str__(self):
        return 'server error. code: %s message: %s' % (self.code, self.message)


class ConnectionRefusedError(Exception):
    pass


class NoConnectedStoragePoolError(DumpChainsError):
    pass


class ChainError(DumpChainsError):
    def __init__(self, volumes_children):
        self.volumes_children = volumes_children


class DuplicateParentError(ChainError):
    description = ("More than one volume pointing to the same parent volume "
                   "e.g: (_BLANK_UUID<-a), (a<-b), (a<-c)")


class NoBaseVolume(ChainError):
    description = ("no volume with a parent volume Id _BLANK_UUID found e.g: "
                   "(a<-b), (b<-c)")


class ChainLoopError(ChainError):
    description = ("A loop found in the volume chain. This happens if a "
                   "volume points to one of it's parent volume e.g.: "
                   "(BLANK_UUID<-a), (a<-b), (b<-c), (c<-a)")


class OrphanVolumes(ChainError):
    description = ("There are volumes that are part of an image and are "
                   "pointing to volumes which are not part of the chain e.g: "
                   "(BLANK_UUID<-a), (a<-b), (c<-d)")


@expose(_NAME)
def dump_chains(*args):
    """
    dump-volume-chains
    Query VDSM about the existing structure of image volumes and prints
    them in an ordered fashion with optional additional info per volume.
    """
    parsed_args = _parse_args(args)
    server = _connect_to_server(parsed_args.host, parsed_args.port,
                                parsed_args.use_ssl)
    image_chains, volumes_info = _get_volumes_chains(
        server, parsed_args.sd_uuid)

    _print_volume_chains(image_chains, volumes_info)


def _connect_to_server(host, port, use_ssl):
    host_port = "%s:%s" % (host, port)
    try:
        return vdscli.connect(host_port, use_ssl)
    except socket.error as e:
        if e[0] == errno.ECONNREFUSED:
            raise ConnectionRefusedError(
                "Connection to %s refused" % (host_port,))
        raise


def _parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('sd_uuid', help="storage domain UUID")
    parser.add_argument('-u', '--unsecured', action='store_false',
                        dest='use_ssl', default=True,
                        help="use unsecured connection")
    parser.add_argument('-H', '--host', default=vdscli.ADDRESS)
    parser.add_argument('-p', '--port', default=vdscli.PORT)

    return parser.parse_args(args=args[1:])


def _get_volume_info(server, vol_uuid, img_uuid, sd_uuid, sp_uuid):
    res = _call_server(server.getVolumeInfo, sd_uuid, sp_uuid, img_uuid,
                       vol_uuid)
    return res['info']


def _get_volumes_chains(server, sd_uuid):
    sp_uuid = _get_sp_uuid(server)
    images_uuids = _get_all_images(server, sd_uuid)

    image_chains = {}  # {image_uuid -> vol_chain}
    volumes_info = {}  # {vol_uuid-> vol_info}

    for img_uuid in images_uuids:
        volumes = _get_volumes_for_image(server, img_uuid, sd_uuid, sp_uuid)

        # to avoid 'double parent' bug here we don't use a dictionary
        volumes_children = []  # [(parent_vol_uuid, child_vol_uuid),]

        for vol_uuid in volumes:
            vol_info = _get_volume_info(server, vol_uuid, img_uuid, sd_uuid,
                                        sp_uuid)
            volumes_info[vol_uuid] = vol_info

            parent_uuid = vol_info['parent']
            volumes_children.append((parent_uuid, vol_uuid))

        try:
            image_chains[img_uuid] = _build_volume_chain(volumes_children)
        except ChainError as e:
            image_chains[img_uuid] = e

    return image_chains, volumes_info


def _get_all_images(server, sd_uuid):
    res = _call_server(server.getImagesList, sd_uuid)
    return res['imageslist']


def _get_volumes_for_image(server, img_uuid, sd_uuid, sp_uuid):
    res = _call_server(server.getVolumesList, sd_uuid, sp_uuid, img_uuid)
    return res['uuidlist']


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


def _get_sp_uuid(server):
    """there can be only one storage pool in a single VDSM context"""
    pools = _call_server(server.getConnectedStoragePoolsList)
    try:
        sp_uuid, = pools['poollist']
    except ValueError:
        if not pools['poollist']:
            raise NoConnectedStoragePoolError('There is no connected storage '
                                              'pool to this server')
    else:
        return sp_uuid


def _print_volume_chains(image_chains, volumes_info):
    print()
    print('Images volume chains (base volume first)')
    for img_uuid, vol_chain in image_chains.iteritems():
        print()
        _print_line(img_uuid, 'image:')
        print()
        if isinstance(vol_chain, ChainError):
            chain_err = vol_chain
            _print_line(chain_err.description)
            _print_line('Volumes and children:')
            print()
            for parent, child in chain_err.volumes_children:
                _print_line('- %s <- %s' % (parent, child))
                _print_vol_info(volumes_info[child])
                print()
        else:
            for vol in vol_chain:
                _print_line('- ' + vol)
                _print_vol_info(volumes_info[vol])
                print()


def _print_vol_info(volume_info):
    robust_volume_info = defaultdict(lambda: '(missing)', volume_info)
    info_fmt = "status: {d[status]}, voltype: {d[voltype]}, " \
               "format: {d[format]}, legality: {d[legality]}, type: {d[type]}"
    formatted_info = info_fmt.format(d=robust_volume_info)
    _print_line('  ' + formatted_info)


def _print_line(body, title=''):
    print('{0:^13}{1}'.format(title, body))


def _call_server(method, *args):
    res = method(*args)
    if res['status']['code']:
        raise ServerError(res)
    return res
