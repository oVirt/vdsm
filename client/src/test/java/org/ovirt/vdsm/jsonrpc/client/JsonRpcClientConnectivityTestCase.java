package org.ovirt.vdsm.jsonrpc.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.fail;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.ovirt.vdsm.jsonrpc.client.TestJsonRpcClient.jsonFromString;

import java.io.IOException;
import java.net.ConnectException;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import org.codehaus.jackson.JsonNode;
import org.junit.Ignore;
import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.internal.ResponseWorker;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorFactory;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorType;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient.MessageListener;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryPolicy;

// it takes too long to have it as part of build process
@Ignore
public class JsonRpcClientConnectivityTestCase {

    private final static String HOSTNAME = "127.0.0.1";
    private final static int PORT = 54321;
    private final static int CONNECTION_RETRY = 1;
    private final static int TIMEOUT = 1000;
    private final static int TIMEOUT_SEC = 3;
    
    private Reactor getReactor() throws ClientConnectionException {
        return ReactorFactory.getReactor(null, ReactorType.STOMP);
    }

    @Test(expected = ConnectException.class)
    public void testDelayedConnect() throws Throwable {
        // Given
        Reactor reactor = getReactor();
        final ReactorClient client = reactor.createClient(HOSTNAME, 3333);
        client.setRetryPolicy(new RetryPolicy(TIMEOUT, CONNECTION_RETRY, IOException.class));
        ResponseWorker worker = ReactorFactory.getWorker();
        JsonRpcClient jsonClient = worker.register(client);
        JsonRpcRequest request = mock(JsonRpcRequest.class);
        when(request.getId()).thenReturn(mock(JsonNode.class));

        assertNotNull(jsonClient);
        assertFalse(client.isOpen());

        try {
            // When
            jsonClient.call(request);

            // Then
            fail();
        } catch (ClientConnectionException e) {
            throw e.getCause();
        }
    }

    @SuppressWarnings("unchecked")
    @Test
    public void testRetryMessageSend() throws IOException, InterruptedException, ExecutionException, TimeoutException,
            ClientConnectionException {
        // Given
        Reactor reactorForListener = getReactor();
        Future<ReactorListener> futureListener = reactorForListener.createListener(HOSTNAME, PORT,
                new ReactorListener.EventListener() {
                    @Override
                    public void onAcccept(final ReactorClient client) {
                        client.addEventListener(new MessageListener() {
                            @Override
                            public void onMessageReceived(byte[] message) {
                                // if timing is wrong ignore the message
                            }
                        });
                    }
                });

        ReactorListener listener = futureListener.get(TIMEOUT_SEC, TimeUnit.SECONDS);

        Reactor reactor = getReactor();
        final ReactorClient client = reactor.createClient(HOSTNAME, PORT);
        client.setRetryPolicy(new RetryPolicy(TIMEOUT, CONNECTION_RETRY, IOException.class));
        ResponseWorker worker = ReactorFactory.getWorker();
        JsonRpcClient jsonClient = worker.register(client);
        jsonClient.setRetryPolicy(new RetryPolicy(TIMEOUT, 2));
        JsonRpcRequest request = mock(JsonRpcRequest.class);
        when(request.getId()).thenReturn(mock(JsonNode.class));

        assertNotNull(jsonClient);
        assertFalse(client.isOpen());

        // When
        Future<JsonRpcResponse> future = jsonClient.call(request);
        listener.close();
        reactorForListener.close();
        client.close();

        // Then
        JsonRpcResponse response = future.get();
        assertNotNull(response);

        ResponseDecomposer decomposer = new ResponseDecomposer(response);
        Map<String, Object> error = decomposer.decomposeError();
        assertNotNull(error);
        Map<String, Object> status = (Map<String, Object>) error.get("status");
        assertEquals(5022, status.get("code"));
    }

    @SuppressWarnings("unchecked")
    @Test
    public void testBulkRetryMessageSend() throws InterruptedException, ExecutionException, TimeoutException,
            IOException, ClientConnectionException {
        // Given
        Reactor reactorForListener = getReactor();
        Future<ReactorListener> futureListener = reactorForListener.createListener(HOSTNAME, PORT + 1,
                new ReactorListener.EventListener() {
                    @Override
                    public void onAcccept(final ReactorClient client) {
                        client.addEventListener(new MessageListener() {
                            @Override
                            public void onMessageReceived(byte[] message) {
                                // if timing is wrong ignore the message
                            }
                        });
                    }
                });

        ReactorListener listener = futureListener.get(TIMEOUT_SEC, TimeUnit.SECONDS);

        Reactor reactor = getReactor();
        final ReactorClient client = reactor.createClient(HOSTNAME, PORT + 1);
        client.setRetryPolicy(new RetryPolicy(TIMEOUT, CONNECTION_RETRY, IOException.class));
        ResponseWorker worker = ReactorFactory.getWorker();
        JsonRpcClient jsonClient = worker.register(client);
        jsonClient.setRetryPolicy(new RetryPolicy(TIMEOUT, 2));

        final JsonNode params = jsonFromString("{\"text\": \"Hello World\"}");
        final JsonNode id1 = jsonFromString("123");
        final JsonNode id2 = jsonFromString("1234");
        JsonRpcRequest[] requests =
                new JsonRpcRequest[] { new JsonRpcRequest("echo", params, id1), new JsonRpcRequest("echo", params, id2) };

        assertNotNull(jsonClient);
        assertFalse(client.isOpen());

        // When
        Future<List<JsonRpcResponse>> future = jsonClient.batchCall(Arrays.asList(requests));
        listener.close();
        reactorForListener.close();
        client.close();

        // Then
        List<JsonRpcResponse> responses = future.get();
        assertNotNull(responses);
        assertEquals(2, responses.size());

        for (JsonRpcResponse response : responses) {
            ResponseDecomposer decomposer = new ResponseDecomposer(response);
            Map<String, Object> error = decomposer.decomposeError();
            assertNotNull(error);
            Map<String, Object> status = (Map<String, Object>) error.get("status");
            assertEquals(5022, status.get("code"));
        }
    }
}
