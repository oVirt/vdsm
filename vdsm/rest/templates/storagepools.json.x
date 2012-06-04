{
  "storagepools":
  [
#set first = 1
#for $resource in $resources
#if first == 1# #set first = 0# #else#    ,#end if#
    {
      "id": "$resource.uuid",
      "href": "/api/storagepools/$resource.uuid",
      "pool_status": "$resource.info['pool_status']",
    #if $resource.info['pool_status'] == "connected"
      "name": "$resource.info['name']",
      "isoprefix": "$resource.info['isoprefix']",
      "master_uuid": "$resource.info['master_uuid']",
      "version": "$resource.info['version']",
      "spm_id": "$resource.info['spm_id']",
      "type": "$resource.info['type']",
      "master_ver": "$resource.info['master_ver']",
      "lver": "$resource.info['lver']",
      "domains": [
    #set first = 1
    #for $sdUUID, $sdStats in $resource.dominfo.items()
    #if first == 1# #set first = 0# #else#    ,#end if#
        {
          "id": "$sdUUID", "href": "/api/storagedomains/$sdUUID",
    #if $sdStats['status'] == "Active"
          "diskfree": ${sdStats['diskfree']},
          "disktotal": ${sdStats['disktotal']},
    #end if
          "status": "${sdStats['status']}"
        }
    #end for
      ],
    #end if
      "actions": {
        "links": [ {
          "rel": "destroy",
          "href": "/api/storagepools/$resource.uuid/destroy"
        }, {
          "rel": "disconnect",
          "href": "/api/storagepools/$resource.uuid/disconnect"
        }, {
          "rel": "spmStart",
          "href": "/api/storagepools/$resource.uuid/spmStart"
        }, {
          "rel": "spmStop",
          "href": "/api/storagepools/$resource.uuid/spmStop"
        } ]
      }
    }
#end for
  ],
  "actions": {
    "links": [ {
      "rel": "create",
      "href": "/api/storagepools/create"
    }, {
      "rel": "connect",
      "href": "/api/storagepools/connect"
    } ]
  }
}
