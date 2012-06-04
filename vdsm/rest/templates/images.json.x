{
  "images":
  [
#set first = 1
#for $resource in $resources
#if first == 1# #set first = 0# #else#    ,#end if#
    {
      "id": "$resource.uuid",
      "href": "/api/storagedomains/$resource.sdUUID/images/$resource.uuid",
      "links": [ {
        "rel": "volumes",
        "href": "/api/storagedomains/$resource.sdUUID/images/$resource.uuid/volumes"
      } ],
      "actions": {
        "links": [ {
          "rel": "delete",
          "href: "/api/storagedomains/$resource.sdUUID/images/$resource.uuid/delete"
        } ]
      }
    }
#end for
  ],
  "actions": {
    "links": []
  }
}
