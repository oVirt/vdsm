package org.ovirt.vdsm.jsonrpc.client;

import static org.junit.Assert.assertEquals;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.codehaus.jackson.JsonParseException;
import org.codehaus.jackson.map.JsonMappingException;
import org.codehaus.jackson.map.ObjectMapper;
import org.codehaus.jackson.type.TypeReference;
import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcRequest;
import org.ovirt.vdsm.jsonrpc.client.RequestBuilder;

public class RequestBuilderTestCase {

    @SuppressWarnings("unchecked")
    @Test
    public void testSimpleRequest() throws JsonParseException, JsonMappingException, IOException {
        // given
        String methodName = "Task.getInfo";
        String taskId = "1234";

        // when
        JsonRpcRequest request = new RequestBuilder(methodName).withParameter("taskID", taskId).build();

        // then
        assertEquals(methodName, request.getMethod());
        Map<String, Object> jsonData =
                new ObjectMapper().readValue(request.toJson(), new TypeReference<HashMap<String, Object>>() {
                });
        assertEquals(methodName, jsonData.get("method"));
        Map<String, Object> params = (Map<String, Object>) jsonData.get("params");
        assertEquals(taskId, params.get("taskID"));
    }

    @SuppressWarnings("unchecked")
    @Test
    public void testRequestwithMap() throws JsonParseException, JsonMappingException, IOException {
        // given
        String methodName = "VM.create";
        Map<String, Object> map = new HashMap<>();
        map.put("acpiEnable", true);
        map.put("cpuShares", "shares");
        Map<String, String> customMap = new HashMap<>();
        customMap.put("customName", "customeValue");
        map.put("custom", customMap);
        List<Map<String, String>> devices = new ArrayList<>();
        Map<String, String> device = new HashMap<>();
        device.put("domainID", "myId");
        devices.add(device);
        map.put("devices", devices);
        map.put("display", "VmDisplayType");
        map.put("kvmEnable", true);
        map.put("memSize", 1024);
        map.put("nice", 10);
        map.put("smp", 1);
        map.put("smpCoresPerSocket", 2);
        map.put("smpThreadsPerCore", 1);
        map.put("timeOffset", 5);
        map.put("transparentHugePages", false);
        map.put("vmId", UUID.randomUUID().toString());
        map.put("vmName", "MyVm");
        map.put("vmType", "kvm");

        // when
        JsonRpcRequest request = new RequestBuilder(methodName).withParameter("vmParams", map).build();

        // then
        Map<String, Object> jsonData =
                new ObjectMapper().readValue(request.toJson(), new TypeReference<HashMap<String, Object>>() {
                });
        assertEquals(methodName, jsonData.get("method"));
        Map<String, Object> params = (Map<String, Object>) jsonData.get("params");
        Map<String, Object> vmParams = (Map<String, Object>) params.get("vmParams");
        assertEquals(map.get("acpiEnable"), vmParams.get("acpiEnable"));
        assertEquals(map.get("memSize"), vmParams.get("memSize"));
    }
}
