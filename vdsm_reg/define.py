errCode = { 'noVM':         {'status': {'code': 1, 'message': 'Desktop does not exist'}},
            'nfsErr':       {'status': {'code': 3, 'message': 'Image repository access timeout'}},
            'exist':        {'status': {'code': 4, 'message': 'Desktop already exists'}},
            'noVmType':     {'status': {'code': 5, 'message': 'Unsupported virtual machine type'}},
            'down':         {'status': {'code': 6, 'message': 'Desktop is down'}},
            'copyerr':      {'status': {'code': 7, 'message': 'Copy failed'}},
            'sparse':       {'status': {'code': 8, 'message': 'Sparse creation failed'}},
            'createErr':    {'status': {'code': 9, 'message': 'Error creating the requested Desktop'}},
            'noConPeer':    {'status': {'code':10, 'message': 'Could not connect to peer VDS'}},
            'MissParam':    {'status': {'code':11, 'message': 'Missing required parameter'}},
            'migrateErr':   {'status': {'code':12, 'message': 'Fatal error during migration'}},
            'imageErr':     {'status': {'code':13, 'message': 'Drive image file could not be found'}},
            'outOfMem':     {'status': {'code':14, 'message': 'Not enough free memory to create Desktop'}},
            'unexpected':   {'status': {'code':16, 'message': 'Unexpected exception'}},
            'unsupFormat':  {'status': {'code':17, 'message': 'Unsupported image format'}},
            'ticketErr':    {'status': {'code':18, 'message': 'Error while setting spice ticket'}},
            'recovery':     {'status': {'code':100, 'message': 'Recovering from crash or still initializing'}},
            'installErr':   {'status': {'code':101, 'message': 'Vds not operational. Check logs, repair it, and restart'}},
            'tmp': {}
            }
doneCode = {'code': 0, 'message': 'Done'}
nullCode = {'code': 0, 'message': ''}


#confFile = 'vdsm.conf'
loggerConf = 'logger.conf'
installPath = '/usr/share/vdsm/'
relPath = './'

Kbytes = 1024
Mbytes = 1024 * Kbytes

drives = ['hda', 'hdb', 'hdc', 'hdd', 'cdrom']
requiredParams = ['vmId', 'hda', 'memSize', 'macAddr', 'display']
class myException(Exception): pass

#exitCodes
ERROR = 1
NORMAL = 0
