import os
import traceback

BLANK_UUID = '00000000-0000-0000-0000-000000000000'
DC = '/rhev/data-center/'
CDROM_IMAGE = '11111111-1111-1111-1111-111111111111'

class StorageTable:

    def __init__(self, server):
        self.serverConncetion = server
        self.columns = ['Vms', 'Domains', 'Images', 'Volumes', 'Template']
        self._buildStorageTable()

    def _getVms(self, pool, vmUUIDs):
        disks = []
        images = []

        for vmUUID in vmUUIDs:
            vm_images_links = os.listdir(DC + '%s/vms/%s/' % (pool, vmUUID))
            for vm_image_link in vm_images_links:
                ovf = open(DC + '%s/vms/%s/%s' % (pool, vmUUID, vm_image_link)).read()
                cont = ovf.split('ovf:fileRef=\"')
                vmName = cont[-1].split('<Name>')[-1].split('</Name>')[0]

                # Avoiding diskless VMs
                if len(cont) < 2:
                    continue

                self.vmList[vmUUID] = {'Name':vmName, 'Refs': []}
                for x in range(1, len(cont)):
                    image = cont[x].split(' ')[0].split('/')[0]
                    if not image in images:
                        sd = self.serverConncetion.getImageDomainsList(pool,image)['domainslist']
                        if len(sd) > 0:
                            sd = sd[0]
                            images.append(image)
                            self._setSdInfo(sd)
                            disks.append([sd,image])
                            self.vmList[vmUUID]['Refs'].append([sd,image])

        self._getImages(disks)

    def _setSdInfo(self, sd):

        if not sd in self.domainsList.keys():
            sd_info = self.serverConncetion.getStorageDomainInfo(sd)['info']
            self.domainsList[sd] = {'Name':sd_info['name'], 'Role':sd_info['role'], 'Refs':[]}

    def _getSds(self, pool):

        disks = []
        sds = self.serverConncetion.getStorageDomainsList(pool)
        for sd in sds['domlist']:
            images = []
            imagesList = self.serverConncetion.getImagesList(sd)['imageslist']
            self._setSdInfo(sd)

            for image in imagesList:
                images.append(image)
                disks.append([sd, image])
                if image != CDROM_IMAGE:
                    self.domainsList[sd]['Refs'].append(image)
        self._getImages(disks)

    def _getImages(self, disks):

        for disk in disks:
            if disk[1] == CDROM_IMAGE:
                continue
            else:
                sd, image = disk
                imageDom = '%s:%s' % (sd, image)
                if not imageDom in self.imagesList.keys():
                    self.imagesList[imageDom] = []

                self._getVols(self.pool, sd, image)

    def _getVols(self, pool, sd, image):
        imageDom = '%s:%s' % (sd, image)
        volumes = self.serverConncetion.getVolumesList(sd, pool, image)
        vollen = len(volumes['uuidlist'])

        volParents = {}

        leafvol = ''

        for vol in volumes['uuidlist']:
            res = self.serverConncetion.getVolumeInfo(sd, pool, image, vol)
            volParents[vol] = res['info']['parent']
            imageDomVol = '%s:%s:%s' % (sd, image,vol)
            self.volumesList[imageDomVol] = []

            if res['info']['voltype'] == 'LEAF':
                leafvol = vol

            if res['info']['voltype'] == 'SHARED':
                leafvol = vol
                self.volumesList[imageDomVol].append('Template')

        self.imagesList[imageDom] = self._buildVolumesChain(leafvol,vollen,volParents, sd, image)

    def _buildVolumesChain(self, leafvol, vollen, volParents, sd, image):

        volChain = vollen*[None]

        volChain[(vollen-1)]=leafvol
        next = leafvol
        for k in range(1,(vollen+1)):
            prev = next
            next = volParents[prev]
            if next in volParents.keys():
                volChain[(vollen-1-k)]=next
            else:
                if next == BLANK_UUID:
                    app = 'Template independent'
                else:
                    app = '%s' % (next)

                imageDomVol = '%s:%s:%s' % (sd, image,prev)
                templList = self.volumesList[imageDomVol]
                if len(templList) == 0:
                    templList.append(app)

        return volChain



    def _buildStorageTable(self):

        self.domainsList = {}
        self.vmList = {}
        self.imagesList = {}
        self.volumesList = {}
        self.tbl = {}
        for header in self.columns:
            self.tbl[header] = []

        pools = self.serverConncetion.getConnectedStoragePoolsList()
        self.pool = pools['poollist'][0]


        # Check if its possible to access the ovf files
        try:
            files = os.listdir(DC + '%s/vms/' % (self.pool,))
            self.canAccessVms = True
        except (OSError, IOError):
            self.canAccessVms = False

        if self.canAccessVms:
            self._getVms(self.pool, files)
        else:
            self._getSds(self.pool)

        self._prepareTable()

    def _prepareTable(self):
        '''Prepare the table for printing'''

        if self.canAccessVms:
            colStart = 'Vms'
            startObj = self.vmList
        else:
            colStart = 'Domains'
            startObj = self.domainsList

        for obj, obj_data in startObj.items():
            row = 0

            obj_data = dict(obj_data) # copy
            refs = obj_data.pop('Refs')
            self.tbl[colStart].append([obj] + obj_data.values())

            if len(refs) == 0:
                self.tbl['Images'].append([])
                self.tbl['Volumes'].append([])
                self.tbl['Template'].append([])

            for ref in refs:
                if self.canAccessVms:
                    sdUUID, image = ref
                    if row == 0:
                        sd_data = dict(self.domainsList[sdUUID])
                        del sd_data['Refs']
                        self.tbl['Domains'].append( [sdUUID] + sd_data.values() )
                    else:
                        self.tbl['Vms'].append([])
                        self.tbl['Domains'].append([])
                else:
                    sdUUID = obj
                    image = ref

                    if row == 0:
                        pass
                    else:
                        self.tbl['Domains'].append([])

                imageDom = '%s:%s' % (sdUUID, image)
                self.tbl['Images'].append([image])
                volsToAdd = self.imagesList[imageDom]
                self.tbl['Volumes'].append(volsToAdd)
                vol = volsToAdd[0]
                domImageVol = '%s:%s:%s' % (sdUUID, image, vol)

                templToAdd = self.volumesList.get( domImageVol, [] )
                self.tbl['Template'].append(templToAdd)
                row += 1


    def _printRowSep(self, rowSep, columnNum):
        sepLine = '+' + ( rowSep + '+' ) * columnNum
        print sepLine

    def _printTableSegment(self, depth, alignCenter=False, printHeader=False):
        '''Printing the Table'''
        UUID_LEN = 36

        row = 0
        rows = 1
        rowSep = (UUID_LEN+2) * '-'
        colStart = 0

        if not self.canAccessVms:
            colStart = 1
        columnNum = len(self.columns) - colStart

        if printHeader:
            self._printRowSep(rowSep, columnNum)

        while row <= rows:
            line = '|'

            for column in self.columns[colStart:]:
                if printHeader:
                    printObject = [column]
                else:
                    printObject = self.tbl[column][depth]

                if len(printObject) > rows:
                    rows = len(printObject)

                try:
                    adding = printObject[row]
                except IndexError:
                    adding = ''

                if alignCenter:
                    adding = adding.center( UUID_LEN )
                else:
                    adding = adding.ljust( UUID_LEN )

                line = line + ' %s |' % (adding)

            print '%s' % (line)
            row += 1

        self._printRowSep(rowSep, columnNum)


    def show(self):
        '''Show the Storage Table'''

        try:
            depth = 0
            self._printTableSegment(0, alignCenter=True, printHeader=True)

            for depth in range(0, len(self.tbl['Domains'])):
                self._printTableSegment(depth)

            return 0, ''
        except:
            return 1, traceback.format_exc()

if __name__ == '__main__':
    import vdscli
    import sys

    rc, msg = StorageTable(vdscli.connect()).show()
    if rc:
        print >>sys.stderr, msg
    sys.exit(rc)
