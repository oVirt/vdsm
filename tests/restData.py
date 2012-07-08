class APIData(object):
    def __init__(self, obj, meth, data):
        self.obj = obj
        self.meth = meth
        self.data = data

testRootIndex_apidata = [
  APIData('Global', 'getCapabilities', {
    'status': {'code': 0},
    'info': {'software_version': '4.9',
             'software_revision': '0'}})
]

testRootIndex_response_xml = """
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<api>
  <product_info>
    <name>vdsm</name>
    <vendor>oVirt</vendor>
    <version major="4" minor="9" build="0" revision="0"/>
  </product_info>
  <link href="/api/storageconnectionrefs" rel="storageconnectionrefs"/>
  <link href="/api/storagedomains" rel="storagedomains"/>
  <link href="/api/storagepools" rel="storagepools"/>
  <link href="/api/tasks" rel="tasks"/>
</api>
"""

testRootIndex_response_json = """
{
  "product_info": {
    "version": {
      "major": 4,
      "minor": 9,
      "build": 0,
      "revision": "0"
    },
    "name": "vdsm",
    "vendor": "oVirt"
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
"""

testStorageConnectionsIndex_apidata = [
  APIData('ConnectionRefs', 'statuses', {
    'status': {'code': 0},
    'connectionslist': {
      '3786d8b5-358a-4441-8df9-6cf61bde0f42': {
        'connectionInfo': {
          'type': 'localfs',
          'params': {'path': '/dev/null'},
        },
        'lastError': [0, 'test message'],
        'connected': True}}
  })
]

testStorageConnectionsIndex_response_json = """
{
  "storageconnectionrefs":
  [

    {
      "id": "3786d8b5-358a-4441-8df9-6cf61bde0f42",
      "href": "/api/storageconnectionrefs/3786d8b5-358a-4441-8df9-6cf61bde0f42",
      "type": "localfs",
      "parameters": {
        "path": "/dev/null"
      },
      "lastError": {
        "code": 0,
        "message": "test message"
      },
      "connected": "True",
      "actions": {
        "links": [ {
          "rel": "release",
          "href": "/api/storageconnectionrefs/3786d8b5-358a-4441-8df9-6cf61bde0f42/release"
        } ]
      }
    }
  ],
  "actions": {
    "links": [ {
      "rel": "acquire",
      "href": "/api/storageconnectionrefs/acquire"
    } ]
  }
}
"""

testStorageConnectionsIndex_response_xml = """
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<storageconnectionrefs>
  <storageconnectionref href="/api/storageconnectionrefs/3786d8b5-358a-4441-8df9-6cf61bde0f42" id="3786d8b5-358a-4441-8df9-6cf61bde0f42">
    <type>localfs</type>
    <parameters>
      <path>/dev/null</path>
    </parameters>
    <lastError>
      <code>0</code>
      <message>test message</message>
    </lastError>
    <connected>true</connected>
    <actions>
      <link href="/api/storageconnectionrefs/3786d8b5-358a-4441-8df9-6cf61bde0f42/release" rel="release"/>
    </actions>
  </storageconnectionref>
  <actions>
    <link href="/api/storageconnectionrefs/acquire" rel="acquire"/>
  </actions>
</storageconnectionrefs>
"""

testStorageConnectionAcquire_request_json = """
{ "id": "929326c2-f062-47b7-8c5d-5a677690ebd7",
  "type": "localfs",
  "parameters": { "path": "/dev/null" } }
"""

testStorageConnectionAcquire_request_xml = """
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<action>
  <id>929326c2-f062-47b7-8c5d-5a677690ebd7</id>
  <type>localfs</type>
  <parameters>
    <path>/dev/null</path>
  </parameters>
</action>
"""

testStorageConnectionAcquire_apidata = [
  APIData('ConnectionRefs', 'acquire', {
    'status': {'code': 0, 'message': 'Done'},
    'results': {'929326c2-f062-47b7-8c5d-5a677690ebd7': 0},
  })
]

testStorageConnectionAcquire_response_json = """
{
  "status": {
    "code": 0,
    "state": "Done"
    ,"detail": "{\'status\': {\'message\': \'Done\', \'code\': 0}, \'results\': {\'929326c2-f062-47b7-8c5d-5a677690ebd7\': 0}}"
  }
}
"""
testStorageConnectionAcquire_response_xml = """
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<action>
  <status>
    <code>0</code>
    <state>Done</state>
    <detail>{\'status\': {\'message\': \'Done\', \'code\': 0}, \'results\': {\'929326c2-f062-47b7-8c5d-5a677690ebd7\': 0}}</detail>
  </status>
</action>
"""

testStorageDomainIndex_apidata = [
  APIData('Global', 'getStorageDomains', {
    'status': {'code': 0},
    'domlist': ['a492dfad-be51-4d3c-afbe-2237ef8b46ce']
  }),
  APIData('StorageDomain', 'getInfo', {
    'status': {'code': 0},
    'info': {'name': 'testdomain', 'type': 'LOCALFS', 'class': 'Data',
             'role': 'Master', 'remotePath': '/dev/null', 'version': 1,
             'master_ver': 2, 'lver': 3, 'spm_id': -1,
             'pool': ['4633eae7-47ed-4215-bbf1-8d9e8a255fa7']}
  }),
]
testStorageDomainIndex_response_json = """
{
  "id": "a492dfad-be51-4d3c-afbe-2237ef8b46ce",
  "href": "/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce",
  "name": "testdomain",
  "type": "LOCALFS",
  "class": "Data",
  "role": "Master",
  "remotePath": "/dev/null",
  "version": "1",
  "master_ver": "2",
  "lver": "3",
  "spm_id": "-1",
  "storagepool": {
    "id": "4633eae7-47ed-4215-bbf1-8d9e8a255fa7",
    "href": "/api/storagepools/4633eae7-47ed-4215-bbf1-8d9e8a255fa7"
  },
  "links": [ {
    "rel": "images",
    "href": "/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/images"
  }, {
    "rel": "volumes",
    "href": "/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/volumes"
  } ],
  "actions": {
    "links": [ {
      "rel": "format",
      "href": "/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/format"
    }, {
      "rel": "attach",
      "href": "/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/attach"
    }, {
      "rel": "detach",
      "href": "/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/detach"
    }, {
      "rel": "activate",
      "href": "/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/activate"
    }, {
      "rel": "deactivate",
      "href": "/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/deactivate"
    } ]
  }
}
"""

testStorageDomainIndex_response_xml = """
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<storagedomain href="/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce" id="a492dfad-be51-4d3c-afbe-2237ef8b46ce">
  <name>testdomain</name>
  <type>LOCALFS</type>
  <class>Data</class>
  <role>Master</role>
  <remotePath>/dev/null</remotePath>
  <version>1</version>
  <master_ver>2</master_ver>
  <lver>3</lver>
  <spm_id>-1</spm_id>
  <storagepool id="4633eae7-47ed-4215-bbf1-8d9e8a255fa7" href="/api/storagepools/4633eae7-47ed-4215-bbf1-8d9e8a255fa7"/>
  <link href="/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/images" rel="images"/>
  <link href="/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/volumes" rel="volumes"/>
  <actions>
    <link href="format" rel="/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/format"/>
    <link href="attach" rel="/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/attach"/>
    <link href="detach" rel="/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/detach"/>
    <link href="activate" rel="/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/activate"/>
    <link href="deactivate" rel="/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce/deactivate"/>
  </actions>
</storagedomain>
"""

StorageDomain_testResourceNotFound_apidata = [
  APIData('Global', 'getStorageDomains', {
    'status': {'code': 0},
    'domlist': []
  })
]

testVolumeWalk_apidata = [
  APIData('Global', 'getStorageDomains', {
    'status': {'code': 0},
    'domlist': ['bef7ce5f-b6f3-4c8f-9230-aee006d8c5e4']
  }),
  APIData('StorageDomain', 'getInfo', {
    'status': {'code': 0},
    'info': {'name': 'testdomain', 'type': 'LOCALFS', 'class': 'Data',
             'role': 'Master', 'remotePath': '/dev/null', 'version': 1,
             'master_ver': 2, 'lver': 3, 'spm_id': -1,
             'pool': ['4633eae7-47ed-4215-bbf1-8d9e8a255fa7']}
  }),
  APIData('StorageDomain', 'getImages', {
    'status': {'code': 0},
    'imageslist': ['bcad9af2-9b16-4d61-abbb-1b3ec604e290']
  }),
  APIData('StorageDomain', 'getVolumes', {
    'status': {'code': 0},
    'uuidlist': ['c66957de-983d-412b-90ae-f475bc85c16f']
  }),
  APIData('Image', 'getVolumes', {
    'status': {'code': 0},
    'uuidlist': ['c66957de-983d-412b-90ae-f475bc85c16f']
  }),
  APIData('Volume', 'getInfo', {
    'status': {'code': 0},
    'info': {'description': 'Fake volume', 'voltype': 'LEAF_VOL',
             'type': 'SPARSE_VOL', 'disktype': '8', 'format': 'RAW',
             'apparentsize': 24, 'truesize': 0, 'capacity': 25,
             'ctime': 1300, 'mtime': 1500, 'legality': 'LEGAL',
             'parent': '00000000-0000-0000-0000-000000000000',
             'children': []}
  }),
  APIData('Volume', 'getPath', {
    'status': {'code': 0},
    'path': '/some/long/path'
  }),
]

testTasksIndex_apidata = [
  APIData('Global', 'getAllTasksStatuses', {
    'status': {'code': 0},
    'allTasksStatus': {
      'c878661c-591d-4dab-8ed8-fb08416f0146':
        {'message': '1 jobs completed successfully', 'code': 0,
         'taskResult': 'success', 'taskState': 'finished'},
      '87070fee-f38b-471a-a200-27420460ac97':
        {'message': 'Task is aborted', 'code': 411,
         'taskResult': '', 'taskState': 'aborting'}}
  }),
  APIData('Global', 'getAllTasksInfo', {
    'status': {'code': 0},
    'allTasksInfo': {
      'c878661c-591d-4dab-8ed8-fb08416f0146': {'verb': 'spmStart'},
      '87070fee-f38b-471a-a200-27420460ac97': {'verb': 'createVolume'}}
  }),
]
testTasksIndex_response_json = """
{
  "tasks":
  [

    {
      "id": "c878661c-591d-4dab-8ed8-fb08416f0146",
      "href": "/api/tasks/c878661c-591d-4dab-8ed8-fb08416f0146",
      "verb": "spmStart",
      "message": "1 jobs completed successfully",
      "code": 0,
      "result": "success",
      "state": "finished",
      "actions": {
        "links": [ {
          "rel": "clear",
          "href": "/api/tasks/c878661c-591d-4dab-8ed8-fb08416f0146/clear"
        }, {
          "rel": "revert",
          "href": "/api/tasks/c878661c-591d-4dab-8ed8-fb08416f0146/revert"
        }, {
          "rel": "stop",
          "href": "/api/tasks/c878661c-591d-4dab-8ed8-fb08416f0146/stop"
        } ]
      }
    }
    ,
    {
      "id": "87070fee-f38b-471a-a200-27420460ac97",
      "href": "/api/tasks/87070fee-f38b-471a-a200-27420460ac97",
      "verb": "createVolume",
      "message": "Task is aborted",
      "code": 411,
      "result": "",
      "state": "aborting",
      "actions": {
        "links": [ {
          "rel": "clear",
          "href": "/api/tasks/87070fee-f38b-471a-a200-27420460ac97/clear"
        }, {
          "rel": "revert",
          "href": "/api/tasks/87070fee-f38b-471a-a200-27420460ac97/revert"
        }, {
          "rel": "stop",
          "href": "/api/tasks/87070fee-f38b-471a-a200-27420460ac97/stop"
        } ]
      }
    }
  ],
  "actions": {
    "links": []
  }
}
"""
testTasksIndex_response_xml = """
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<tasks>
    <task href="/api/tasks/c878661c-591d-4dab-8ed8-fb08416f0146" id="c878661c-591d-4dab-8ed8-fb08416f0146">
      <verb>spmStart</verb>
      <message>1 jobs completed successfully</message>
      <code>0</code>
      <result>success</result>
      <state>finished</state>
      <actions>
        <link rel="clear" href="/api/tasks/c878661c-591d-4dab-8ed8-fb08416f0146/clear" />
        <link rel="revert" href="/api/tasks/c878661c-591d-4dab-8ed8-fb08416f0146/revert" />
        <link rel="stop" href="/api/tasks/c878661c-591d-4dab-8ed8-fb08416f0146/stop" />
      </actions>
    </task>
    <task href="/api/tasks/87070fee-f38b-471a-a200-27420460ac97" id="87070fee-f38b-471a-a200-27420460ac97">
      <verb>createVolume</verb>
      <message>Task is aborted</message>
      <code>411</code>
      <result></result>
      <state>aborting</state>
      <actions>
        <link rel="clear" href="/api/tasks/87070fee-f38b-471a-a200-27420460ac97/clear" />
        <link rel="revert" href="/api/tasks/87070fee-f38b-471a-a200-27420460ac97/revert" />
        <link rel="stop" href="/api/tasks/87070fee-f38b-471a-a200-27420460ac97/stop" />
      </actions>
    </task>
    <actions>
    </actions>
</tasks>
"""

testInternalError_apidata = [
  APIData('Global', 'getAllTasksStatuses', {
    'status': {'code': 408, 'message': "Can't load Task Metadata"}})
]

testMissingParam_request_xml = """
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<action>
  <id/>
</action>
"""
testMissingParam_apidata = [
  APIData('Global', 'getConnectedStoragePools', {
    'status': {'code': 0},
    'poollist': ['5aa27616-131e-4dda-b22d-8734805013ca']}),
  APIData('Global', 'getStorageDomains', {
    'status': {'code': 0},
    'domlist': ['6c82f2de-b686-41f2-8846-6d4c7174c50e']}),
  APIData('StorageDomain', 'getInfo', {
    'status': {'code': 0},
    'info': { 'pool': ['foo']}}),
  APIData('Global', 'getVMList', {
    'status': {'code': 0},
    'vmList': [{'vmId': 'c977d7f4-a6b3-4868-9a7b-8b947c3d98a0'}]}),
]

testDeleteContent_apidata = [
  APIData('Global', 'getStorageDomains', {
    'status': {'code': 0},
    'domlist': ['146095d9-b53b-4cf6-81e5-d6497cedde09']
  }),
  APIData('StorageDomain', 'getInfo', {
    'status': {'code': 0},
    'info': { 'pool': ['foo']}}),
  APIData('StorageDomain', 'format', {
    'status': {'code': 0, 'message': 'OK'}}),
]

testDeleteContent_request_json = """
{ "autoDetach": "True" }
"""
