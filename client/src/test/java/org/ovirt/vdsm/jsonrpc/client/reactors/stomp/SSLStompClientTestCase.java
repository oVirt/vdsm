package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.fail;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Random;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.SSLContext;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.TestManagerProvider;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient.MessageListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener.EventListener;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryPolicy;

public class SSLStompClientTestCase {
    private static final String CHAR_LIST = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890";
    private final static int TIMEOUT_SEC = 20;
    private final static String HOSTNAME = "localhost";
    private final static String KEYSTORE_NAME = "keystore";
    private final static String TRUSTSTORE_NAME = "truststore";
    private final static String PASSWORD = "mypass";
    private Reactor listeningReactor;
    private Reactor sendingReactor;
    private TestManagerProvider provider;

    @Before
    public void setUp() throws IOException, GeneralSecurityException {
        this.provider =
                new TestManagerProvider(ClassLoader.getSystemResourceAsStream(KEYSTORE_NAME),
                        ClassLoader.getSystemResourceAsStream(TRUSTSTORE_NAME),
                        PASSWORD);
        SSLContext context = this.provider.getSSLContext();
        this.listeningReactor = new SSLStompReactor(context);
        this.sendingReactor = new SSLStompReactor(context);
    }

    @After
    public void tearDown() throws IOException {
        this.provider.closeStreams();
        this.provider = null;
        this.sendingReactor.close();
        this.listeningReactor.close();
    }

    @Test
    public void testShortMessage() throws InterruptedException, ExecutionException, ClientConnectionException {
        testEcho(generateRandomMessage(16));
    }

    @Test
    public void testLongMessage() throws InterruptedException, ExecutionException, ClientConnectionException {
        testEcho(generateRandomMessage(524288));
    }

    @Test
    public void testConnectionRefused() throws ClientConnectionException, InterruptedException, ExecutionException {
        String message = generateRandomMessage(16);
        int port = 60627;
        final BlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(5);
        ReactorClient client = this.sendingReactor.createClient(HOSTNAME, port);
        client.setRetryPolicy(new RetryPolicy(180000, 0, 1000000));
        client.addEventListener(new ReactorClient.MessageListener() {

            @Override
            public void onMessageReceived(byte[] message) {
                queue.add(message);
            }
        });
        try {
            client.connect();
            fail();
        } catch (ClientConnectionException e) {
            // OK
        }

        Future<ReactorListener> futureListener =
                this.listeningReactor.createListener(HOSTNAME, port, new EventListener() {

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
        client.connect();

        client.sendMessage(message.getBytes());
        byte[] response = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);

        assertNotNull(response);
        assertEquals(message, new String(response, UTF8));

    }

    public static String generateRandomMessage(int length) {
        Random random = new Random();
        StringBuffer randStr = new StringBuffer();
        for(int i=0; i< length; i++){
            int number = random.nextInt(CHAR_LIST.length());
            char ch = CHAR_LIST.charAt(number);
            randStr.append(ch);
        }
        return randStr.toString();
    }

    public void testEcho(String message) throws InterruptedException, ExecutionException, ClientConnectionException {
        final BlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(5);
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

        client.sendMessage(message.getBytes());
        byte[] response = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);

        assertNotNull(response);
        assertEquals(message, new String(response, UTF8));

        client.sendMessage(message.getBytes());
        response = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);

        client.close();
        listener.close();

        assertNotNull(response);
        assertEquals(message, new String(response, UTF8));
    }

}
