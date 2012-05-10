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

errCode = { 'noVM':         {'status': {'code': 1, 'message': 'Virtual machine does not exist'}},
            'nfsErr':       {'status': {'code': 3, 'message': 'Image repository access timeout'}},
            'exist':        {'status': {'code': 4, 'message': 'Virtual machine already exists'}},
            'noVmType':     {'status': {'code': 5, 'message': 'Unsupported VM type'}},
            'down':         {'status': {'code': 6, 'message': 'Virtual machine is down'}},
            'copyerr':      {'status': {'code': 7, 'message': 'Copy failed'}},
            'sparse':       {'status': {'code': 8, 'message': 'sparse creation failed'}},
            'createErr':    {'status': {'code': 9, 'message': 'Error creating the requested virtual machine'}},
            'noConPeer':    {'status': {'code':10, 'message': 'Could not connect to peer VDS'}},
            'MissParam':    {'status': {'code':11, 'message': 'Missing required parameter'}},
            'migrateErr':   {'status': {'code':12, 'message': 'Fatal error during migration'}},
            'imageErr':     {'status': {'code':13, 'message': 'Drive image file %s could not be found'}},
            'outOfMem':     {'status': {'code':14, 'message': 'Not enough free memory to create virtual machine'}},
            'unexpected':   {'status': {'code':16, 'message': 'Unexpected exception'}},
            'unsupFormat':  {'status': {'code':17, 'message': 'Unsupported image format'}},
            'ticketErr':    {'status': {'code':18, 'message': 'Error while setting spice ticket'}},
            'nonresp':      {'status': {'code':19, 'message': 'Guest agent non-responsive'}},
# codes 20-29 are reserved for add/delNetwork
# code 39 was used for: wrongHost - migration destination has an invalid hostname
            'unavail':      {'status': {'code':40, 'message': 'Resource unavailable'}},
            'changeDisk':   {'status': {'code':41, 'message': 'Failed to change disk image'}},
            'destroyErr':   {'status': {'code':42, 'message': 'Virtual machine destroy error'}},
            'fenceAgent':   {'status': {'code':43, 'message': 'Unsupported fencing agent'}},
            'noimpl':       {'status': {'code':44, 'message': 'Not implemented'}},
            'hotplugDisk':  {'status': {'code':45, 'message': 'Failed to hotplug disk'}},
            'hotunplugDisk':{'status': {'code':46, 'message': 'Failed to hotunplug disk'}},
            'migCancelErr': {'status': {'code':47, 'message': 'Migration not in progress'}},
            'snapshotErr':  {'status': {'code':48, 'message': 'Snapshot failed'}},
            'hotplugNic':   {'status': {'code':49, 'message': 'Failed to hotplug NIC'}},
            'hotunplugNic': {'status': {'code':50, 'message': 'Failed to hotunplug NIC'}},
            'migInProgress':{'status': {'code':51, 'message': 'Command not supported during migration'}},
            'mergeErr':     {'status': {'code':52, 'message': 'Merge failed'}},
            'recovery':     {'status': {'code':99, 'message': 'Recovering from crash or Initializing'}},
            }
doneCode = {'code': 0, 'message': 'Done'}

Kbytes = 1024
Mbytes = 1024 * Kbytes

#exitCodes
ERROR = 1
NORMAL = 0
