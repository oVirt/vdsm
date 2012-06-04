{
  "product_info": {
    "version": {
      "major": $resource.product_info['version']['major'],
      "minor": $resource.product_info['version']['minor'],
      "build": $resource.product_info['version']['build'],
      "revision": "$resource.product_info['version']['revision']"
    },
    "name": "$resource.product_info['name']",
    "vendor": "$resource.product_info['vendor']"
  },
  "links": [ {
    "rel": "storageconnectionrefs",
    "href": "/api/storageconnectionrefs"
  }, {
    "rel": "storagedomains",
    "href": "/api/storagedomains"
  }, {
    "rel": "storagepools",
    "href": "/api/storagepools"
  }, {
    "rel": "tasks",
    "href": "/api/tasks"
  } ]
}
