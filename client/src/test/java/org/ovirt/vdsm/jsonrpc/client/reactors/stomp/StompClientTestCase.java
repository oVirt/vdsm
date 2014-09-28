package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.fail;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.SSLStompClientTestCase.generateRandomMessage;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;

import java.io.IOException;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient.MessageListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener.EventListener;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryPolicy;

public class StompClientTestCase {
    private final static int TIMEOUT_SEC = 20;
    private final static String HOSTNAME = "localhost";
    private StompReactor listeningReactor;
    private StompReactor sendingReactor;

    @Before
    public void setUp() throws IOException {
        this.listeningReactor = new StompReactor();
        this.sendingReactor = new StompReactor();
    }

    @After
    public void tearDown() throws IOException {
        this.sendingReactor.close();
        this.listeningReactor.close();
    }

    @Test
    public void testHelloWrold() throws InterruptedException, ExecutionException, ClientConnectionException {
        testEchoMessage(generateRandomMessage(16));
    }

    @Test
    public void testLongMessage() throws InterruptedException, ExecutionException, ClientConnectionException {
        testEchoMessage(generateRandomMessage(524288));
    }

    private void testEchoMessage(String message) throws ClientConnectionException, InterruptedException,
            ExecutionException {
        final BlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(1);
        Future<ReactorListener> futureListener =
                this.listeningReactor.createListener(HOSTNAME, 0, new EventListener() {

                    @Override
                    public void onAcccept(final ReactorClient client) {
                        client.addEventListener(new MessageListener() {
                            @Override
                            public void onMessageReceived(byte[] message) {
                                try {
                                    client.sendMessage(message);
                                } catch (ClientConnectionException e) {
                                    fail();
                                }
                            }
                        });
                    }
                });

        ReactorListener listener = futureListener.get();
        assertNotNull(listener);

        ReactorClient client = this.sendingReactor.createClient(HOSTNAME, listener.getPort());
        client.setRetryPolicy(new RetryPolicy(180000, 0, 1000000));
        client.addEventListener(new ReactorClient.MessageListener() {

            @Override
            public void onMessageReceived(byte[] message) {
                queue.add(message);
            }
        });
        client.connect();

        client.sendMessage(message.getBytes(UTF8));
        byte[] response = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);

        client.close();
        listener.close();

        assertNotNull(response);
        assertEquals(message, new String(response, UTF8));
    }
}
