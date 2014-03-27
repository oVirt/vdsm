package org.ovirt.vdsm.jsonrpc.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.fail;

import java.io.IOException;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.Future;

import org.codehaus.jackson.JsonFactory;
import org.codehaus.jackson.JsonNode;
import org.codehaus.jackson.JsonParser;
import org.codehaus.jackson.map.ObjectMapper;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.internal.ResponseWorker;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.server.JsonRpcServer;

public abstract class TestJsonRpcClient {

    private JsonRpcServer server;
    private Reactor reactor;
    private ResponseWorker responseWorker;
    private JsonRpcClient client;
    private ReactorTestHelper helper;
    private final static int PLAIN_PORT = 9090;

    protected abstract ReactorTestHelper getHelper();

    @Before
    public void setUp() throws Exception {
        server = new JsonRpcServer(PLAIN_PORT);
        helper = getHelper();

        setUpReactor();
        setUpClientPool();
        setUpClient();
    }

    private void setUpReactor() throws Exception {
        this.reactor = this.helper.getReactor();
    }

    private void setUpClientPool() {
        this.responseWorker = new ResponseWorker();
    }

    private void setUpClient() {
        try {
            final ReactorClient rclient = this.reactor.createClient("localhost", PLAIN_PORT);
            this.client = this.responseWorker.register(rclient);
        } catch (Exception e) {
            fail();
        }
    }

    @After
    public void tearDown() throws Exception {
        if (client != null) {
            client.close();
        }
        if (server != null) {
            server.close();
        }
        if (responseWorker != null) {
            responseWorker.close();
        }
    }

    public static JsonNode jsonFromString(String str) {
        final JsonFactory jsonFactory = new ObjectMapper().getJsonFactory();
        try (JsonParser jp = jsonFactory.createJsonParser(str)) {
            return jp.readValueAsTree();
        } catch (Exception e) {
            return null;
        }
    }

    @Test
    public void testPing() throws IOException {
        final JsonNode params = jsonFromString("[]");
        final JsonNode id = jsonFromString("123");

        JsonRpcResponse resp = call(new JsonRpcRequest("ping", params, id));

        assertNotNull(resp);
        assertNull(resp.getError());
    }

    @Test
    public void testEcho() throws IOException {
        final JsonNode params = jsonFromString("{\"text\": \"Hello World\"}");
        final JsonNode id = jsonFromString("123");

        JsonRpcResponse resp = call(new JsonRpcRequest("echo", params, id));

        assertNotNull(resp);
        assertNull(resp.getError());
        assertEquals(resp.getResult().asText(), params.get("text").asText());
    }

    @Test
    public void testBatch() throws IOException {
        final JsonNode params = jsonFromString("{\"text\": \"Hello World\"}");
        final JsonNode id1 = jsonFromString("123");
        final JsonNode id2 = jsonFromString("1234");
        JsonRpcRequest[] requests =
                new JsonRpcRequest[] { new JsonRpcRequest("echo", params, id1), new JsonRpcRequest("echo", params, id2) };

        List<JsonRpcResponse> resps = batchCall(Arrays.asList(requests));

        assertNotNull(resps);
        for (final JsonRpcResponse resp : resps) {
            assertNull(resp.getError());
            assertEquals(resp.getResult().asText(), params.get("text").asText());
        }
    }

    private JsonRpcResponse call(JsonRpcRequest request) {
        try {
            Future<JsonRpcResponse> call = client.call(request);
            return call.get();
        } catch (Exception ex) {
            fail();
            return null;
        }
    }

    private List<JsonRpcResponse> batchCall(List<JsonRpcRequest> requests) {
        try {
            Future<List<JsonRpcResponse>> call = client.batchCall(requests);
            return call.get();
        } catch (Exception ex) {
            fail();
            return null;
        }
    }
}
