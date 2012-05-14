{
#if $resource.code == 0
  "status": {
    "code": $resource.code,
    "state": "$resource.msg"
#if $resource.task
    ,"task": {
      "id": "$resource.task",
      "href": "/api/tasks/$resource.task"
    }
#end if
#if $resource.detail
    ,"detail": "$resource.detail"
#end if
  }
#else
  "fault": {
    "code": $resource.code,
    "reason": "$resource.msg"
#if $resource.detail
    ,"detail": "$resource.detail"
#end if
  }
#end if
}
