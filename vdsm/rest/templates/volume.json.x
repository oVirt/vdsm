{
  "id": "$resource.uuid",
  "href": "/api/storagedomains/$resource.sdUUID/images/$resource.imgUUID/volumes/$resource.uuid",
  "description": "$resource.info['description']",
  "voltype": "$resource.info['voltype']",
  "type": "$resource.info['type']",
  "disktype": "$resource.info['disktype']",
  "format": "$resource.info['format']",
  "path": "$resource.info['path']",
  "apparentsize": $resource.info['apparentsize'],
  "truesize": $resource.info['truesize'],
  "capacity": $resource.info['capacity'],
  "ctime": "$resource.info['ctime']",
  "mtime": "$resource.info['mtime']",
  "legality": "$resource.info['legality']",
  "parent": {
    "id": "$resource.info['parent']",
    "href": "/api/storagedomains/$resource.sdUUID/images/$resource.imgUUID/volumes/$resource.info['parent']"
  },
  "children": [
#set first = 1
#for $c_id in $resource.info['children']
#if first == 1# #set first = 0# #else#    ,#end if#
    { "id": "$c_id", "href": "/api/storagedomains/$resource.sdUUID/images/$resource.imgUUID/volumes/$c_id" }
#end for
  ],
  "actions": {
    "links": [ {
      "rel": "delete",
      "href": "/api/storagedomains/$resource.sdUUID/images/$resource.imgUUID/volumes/$resource.uuid/delete"
    } ]
  }
}
