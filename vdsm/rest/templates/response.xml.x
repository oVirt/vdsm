<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<action>
#if $resource.code == 0
  <status>
    <code>$resource.code</code>
    <state>$resource.msg</state>
#if $resource.task
    <task id="$resource.task" href="/api/tasks/$resource.task" />
#end if
#if $resource.detail
    <detail>$resource.detail</detail>
#end if
  </status>
#else
  <fault>
    <code>$resource.code</code>
    <reason>$resource.msg</reason>
#if $resource.detail
    <detail>$resource.detail</detail>
#end if
  </fault>
#end if
</action>
