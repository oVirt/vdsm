{
  "storagedomains":
  [
#set first = 1
#for $resource in $resources
#if first == 1# #set first = 0# #else#    ,#end if#
    {
      "id": "$resource.uuid",
      "href": "/api/storagedomains/$resource.uuid",
      "name": "$resource.info['name']",
      "type": "$resource.info['type']",
      "class": "$resource.info['class']",
      "role": "$resource.info['role']",
      "remotePath": "$resource.info['remotePath']",
      "version": "$resource.info['version']",
      "master_ver": "$resource.info['master_ver']",
      "lver": "$resource.info['lver']",
      "spm_id": "$resource.info['spm_id']",
    #if $resource.spUUID is not None
      "storagepool": {
        "id": "$resource.spUUID",
        "href": "/api/storagepools/$resource.spUUID"
      },
      "links": [ {
        "rel": "images",
        "href": "/api/storagedomains/$resource.uuid/images"
      }, {
        "rel": "volumes",
        "href": "/api/storagedomains/$resource.uuid/volumes"
      } ],
    #end if
      "actions": {
        "links": [ {
          "rel": "format",
          "href": "/api/storagedomains/$resource.uuid/format"
        }, {
          "rel": "attach",
          "href": "/api/storagedomains/$resource.uuid/attach"
        }, {
          "rel": "detach",
          "href": "/api/storagedomains/$resource.uuid/detach"
        }, {
          "rel": "activate",
          "href": "/api/storagedomains/$resource.uuid/activate"
        }, {
          "rel": "deactivate",
          "href": "/api/storagedomains/$resource.uuid/deactivate"
        } ]
      }
    }
#end for
  ],
  "actions": {
    "links": [ {
      "rel": "create",
      "href": "/api/storagedomains/create"
    } ]
  }
}
