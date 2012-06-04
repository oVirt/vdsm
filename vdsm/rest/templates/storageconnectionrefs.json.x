{
  "storageconnectionrefs":
  [
#set first = 1
#for $resource in $resources
#if first == 1##set first = 0##else#    ,#end if#
    {
      "id": "$resource.uuid",
      "href": "/api/storageconnectionrefs/$resource.uuid",
      "type": "$resource.info['connectionInfo']['type']",
      "parameters": {
    #if $resource.info['connectionInfo']['type'] == 'localfs'
        "path": "$resource.info['connectionInfo']['params']['path']"
    #end if
    #if $resource.info['connectionInfo']['type'] == 'iscsi'
        "target": "$resource.info['connectionInfo']['params']['target']",
        "iface": "$resource.info['connectionInfo']['params']['iface']",
        "credentials": "$resource.info['connectionInfo']['params']['credentials']"
    #end if
    #if $resource.info['connectionInfo']['type'] in ('sharedfs', 'posixfs')
        "spec": "$resource.info['connectionInfo']['params']['spec']",
        "vfsType": "$resource.info['connectionInfo']['params']['vfsType']",
        "options": "$resource.info['connectionInfo']['params']['options']"
    #end if
    #if $resource.info['connectionInfo']['type'] == 'nfs'
        "export": "$resource.info['connectionInfo']['params']['export']",
        "retrans": "$resource.info['connectionInfo']['params']['retrans']",
        "timeout": "$resource.info['connectionInfo']['params']['timeout']",
        "version": "$resource.info['connectionInfo']['params']['version']"
    #end if
      },
      "lastError": {
        "code": $resource.info['lastError'][0],
        "message": "$resource.info['lastError'][1]"
      },
      "connected": "$resource.info['connected']",
      "actions": {
        "links": [ {
          "rel": "release",
          "href": "/api/storageconnectionrefs/$resource.uuid/release"
        } ]
      }
    }
#end for
  ],
  "actions": {
    "links": [ {
      "rel": "acquire",
      "href": "/api/storageconnectionrefs/acquire"
    } ]
  }
}
