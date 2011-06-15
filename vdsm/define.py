errCode = { 'noVM':         {'status': {'code': 1, 'message': 'Virtual machine does not exist'}},
            'nfsErr':       {'status': {'code': 3, 'message': 'Image repository access timeout'}},
            'exist':        {'status': {'code': 4, 'message': 'Virtual machine already exists'}},
            'noVmType':     {'status': {'code': 5, 'message': 'Unsupported VM type'}},
            'down':         {'status': {'code': 6, 'message': 'Virtual machine is down'}},
            'copyerr':      {'status': {'code': 7, 'message': 'Copy failed'}},
            'sparse':       {'status': {'code': 8, 'message': 'sparse creation faild'}},
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
            'unavail':      {'status': {'code':40, 'message': 'Resource unavailable'}},
            'changeDisk':   {'status': {'code':41, 'message': 'Failed to change disk image'}},
            'destroyErr':   {'status': {'code':42, 'message': 'Virtual machine destroy error'}},
            'fenceAgent':   {'status': {'code':43, 'message': 'Unsupported fencing agent'}},
            'noimpl':       {'status': {'code':44, 'message': 'Not implemented'}},
            'recovery':     {'status': {'code':99, 'message': 'Recovering from crash or Initializing'}},
            }
doneCode = {'code': 0, 'message': 'Done'}

Kbytes = 1024
Mbytes = 1024 * Kbytes

#exitCodes
ERROR = 1
NORMAL = 0
