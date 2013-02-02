Documentation for the Storage API Overview
******************************************
.. autoclass:: vdsm.storage.hsm.HSM

File Based
==========
Device
------
.. automethod:: vdsm.storage.hsm.HSM.getDeviceInfo
.. automethod:: vdsm.storage.hsm.HSM.getDeviceList

Volume
------
.. automethod:: vdsm.storage.hsm.HSM.getVolumePath
.. automethod:: vdsm.storage.hsm.HSM.getVolumeSize
.. automethod:: vdsm.storage.hsm.HSM.prepareVolume
.. automethod:: vdsm.storage.hsm.HSM.teardownVolume

Structure
=========
Storage Domain
--------------
.. automethod:: vdsm.storage.hsm.HSM.createStorageDomain
.. automethod:: vdsm.storage.hsm.HSM.validateStorageDomain
.. automethod:: vdsm.storage.hsm.HSM.formatStorageDomain
.. automethod:: vdsm.storage.hsm.HSM.forcedDetachStorageDomain
.. automethod:: vdsm.storage.hsm.HSM.setStorageDomainDescription
.. automethod:: vdsm.storage.hsm.HSM.getStorageDomainInfo
.. automethod:: vdsm.storage.hsm.HSM.getStorageDomainStats
.. automethod:: vdsm.storage.hsm.HSM.getStorageDomainsList

Storage Pool
------------
.. automethod:: vdsm.storage.hsm.HSM.createStoragePool
.. automethod:: vdsm.storage.hsm.HSM.destroyStoragePool
.. automethod:: vdsm.storage.hsm.HSM.disconnectStoragePool
.. automethod:: vdsm.storage.hsm.HSM.getStoragePoolInfo
.. automethod:: vdsm.storage.hsm.HSM.getConnectedStoragePoolsList
.. automethod:: vdsm.storage.hsm.HSM.refreshStoragePool

Volume Group
------------
.. automethod:: vdsm.storage.hsm.HSM.createVG
.. automethod:: vdsm.storage.hsm.HSM.removeVG
.. automethod:: vdsm.storage.hsm.HSM.getVGInfo
.. automethod:: vdsm.storage.hsm.HSM.getVGList


Volumes / Images
----------------
.. automethod:: vdsm.storage.hsm.HSM.getFloppyList
.. automethod:: vdsm.storage.hsm.HSM.getImageDomainsList
.. automethod:: vdsm.storage.hsm.HSM.getImagesList
.. automethod:: vdsm.storage.hsm.HSM.getIsoList
.. automethod:: vdsm.storage.hsm.HSM.getVolumeInfo
.. automethod:: vdsm.storage.hsm.HSM.getVolumesList
.. automethod:: vdsm.storage.hsm.HSM.refreshVolume

Cluster
=======
.. automethod:: vdsm.storage.hsm.HSM.prepareForShutdown

Task Managment
--------------
.. automethod:: vdsm.storage.hsm.HSM.getAllTasksInfo
.. automethod:: vdsm.storage.hsm.HSM.getAllTasksStatuses
.. automethod:: vdsm.storage.hsm.HSM.getTaskInfo
.. automethod:: vdsm.storage.hsm.HSM.getTaskStatus
.. automethod:: vdsm.storage.hsm.HSM.stopTask

Storage Pool Managment
----------------------
.. automethod:: vdsm.storage.hsm.HSM.connectStoragePool
.. automethod:: vdsm.storage.hsm.HSM.spmStart
.. automethod:: vdsm.storage.hsm.HSM.reconstructMaster

Storage Server Managment
------------------------
.. automethod:: vdsm.storage.hsm.HSM.connectStorageServer
.. automethod:: vdsm.storage.hsm.HSM.disconnectStorageServer
.. automethod:: vdsm.storage.hsm.HSM.discoverSendTargets
.. automethod:: vdsm.storage.hsm.HSM.validateStorageServerConnection
.. automethod:: vdsm.storage.hsm.HSM.repoStats
