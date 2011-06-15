Documentation for the Storage API Overview
******************************************
.. autoclass:: storage.hsm.HSM

File Based
==========
Device
------
.. automethod:: storage.hsm.HSM.public_getDeviceInfo
.. automethod:: storage.hsm.HSM.public_getDeviceList

Volume
------
.. automethod:: storage.hsm.HSM.public_getVolumePath
.. automethod:: storage.hsm.HSM.public_getVolumeSize
.. automethod:: storage.hsm.HSM.public_prepareVolume
.. automethod:: storage.hsm.HSM.public_teardownVolume

Structure
=========
Storage Domain
--------------
.. automethod:: storage.hsm.HSM.public_createStorageDomain
.. automethod:: storage.hsm.HSM.public_validateStorageDomain
.. automethod:: storage.hsm.HSM.public_formatStorageDomain
.. automethod:: storage.hsm.HSM.public_forcedDetachStorageDomain
.. automethod:: storage.hsm.HSM.public_setStorageDomainDescription
.. automethod:: storage.hsm.HSM.public_getStorageDomainInfo
.. automethod:: storage.hsm.HSM.public_getStorageDomainStats
.. automethod:: storage.hsm.HSM.public_getStorageDomainsList

Storage Pool
------------
.. automethod:: storage.hsm.HSM.public_createStoragePool
.. automethod:: storage.hsm.HSM.public_destroyStoragePool
.. automethod:: storage.hsm.HSM.public_disconnectStoragePool
.. automethod:: storage.hsm.HSM.public_getStoragePoolInfo
.. automethod:: storage.hsm.HSM.public_getConnectedStoragePoolsList
.. automethod:: storage.hsm.HSM.public_refreshStoragePool

Volume Group
------------
.. automethod:: storage.hsm.HSM.public_createVG
.. automethod:: storage.hsm.HSM.public_removeVG
.. automethod:: storage.hsm.HSM.public_getVGInfo
.. automethod:: storage.hsm.HSM.public_getVGList


Volumes / Images
----------------
.. automethod:: storage.hsm.HSM.public_getFloppyList
.. automethod:: storage.hsm.HSM.public_getImageDomainsList
.. automethod:: storage.hsm.HSM.public_getImagesList
.. automethod:: storage.hsm.HSM.public_getIsoList
.. automethod:: storage.hsm.HSM.public_getVolumeInfo
.. automethod:: storage.hsm.HSM.public_getVolumesList
.. automethod:: storage.hsm.HSM.public_refreshVolume

Cluster
=======
.. automethod:: storage.hsm.HSM.public_prepareForShutdown

Task Managment
--------------
.. automethod:: storage.hsm.HSM.public_getAllTasksInfo
.. automethod:: storage.hsm.HSM.public_getAllTasksStatuses
.. automethod:: storage.hsm.HSM.public_getTaskInfo
.. automethod:: storage.hsm.HSM.public_getTaskStatus
.. automethod:: storage.hsm.HSM.public_restoreTasks
.. automethod:: storage.hsm.HSM.public_stopTask

Storage Pool Managment
----------------------
.. automethod:: storage.hsm.HSM.public_connectStoragePool
.. automethod:: storage.hsm.HSM.public_spmStart
.. automethod:: storage.hsm.HSM.public_reconstructMaster

Storage Server Managment
------------------------
.. automethod:: storage.hsm.HSM.public_connectStorageServer
.. automethod:: storage.hsm.HSM.public_disconnectStorageServer
.. automethod:: storage.hsm.HSM.public_discoverSendTargets
.. automethod:: storage.hsm.HSM.public_validateStorageServerConnection

.. automethod:: storage.hsm.HSM.public_getSessionList
.. automethod:: storage.hsm.HSM.public_getStorageConnectionsList

.. automethod:: storage.hsm.HSM.public_repoStats
