package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.fail;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompCommonClient.DEFAULT_REQUEST_QUEUE;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompCommonClient.DEFAULT_RESPONSE_QUEUE;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.security.cert.Certificate;
import java.util.List;
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

public class SSLStompClientTestCase {
    private static final String CHAR_LIST = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890";
    private static final int TIMEOUT_SEC = 3;
    private static final String HOSTNAME = "localhost";
    private static final String KEYSTORE_NAME = "keystore";
    private static final String TRUSTSTORE_NAME = "truststore";
    private static final String PASSWORD = "mypass";
    private Reactor listeningReactor;
    private Reactor sendingReactor;
    private TestManagerProvider provider;

    @Before
    public void setUp() throws IOException, GeneralSecurityException {
        this.provider = createProvider();
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
    public void testShortMessage() throws InterruptedException, ExecutionException, ClientConnectionException,
            IllegalAccessException {
        testEcho(generateRandomMessage(16));
    }

    @Test
    public void testLongMessage() throws InterruptedException, ExecutionException, ClientConnectionException,
            IllegalAccessException {
        testEcho(generateRandomMessage(524288));
    }

    @Test
    public void testConnectionRefused() throws ClientConnectionException, InterruptedException, ExecutionException {
        String message = generateRandomMessage(16);
        int port = 60627;
        final BlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(5);
        ReactorClient client = this.sendingReactor.createClient(HOSTNAME, port);
        client.setClientPolicy(
                new StompClientPolicy(180000, 0, 1000000, DEFAULT_REQUEST_QUEUE, DEFAULT_RESPONSE_QUEUE));
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

        // get error message
        queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);

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
        for (int i = 0; i < length; i++) {
            int number = random.nextInt(CHAR_LIST.length());
            char ch = CHAR_LIST.charAt(number);
            randStr.append(ch);
        }
        return randStr.toString();
    }

    public static TestManagerProvider createProvider() {
        return new TestManagerProvider(ClassLoader.getSystemResourceAsStream(KEYSTORE_NAME),
                ClassLoader.getSystemResourceAsStream(TRUSTSTORE_NAME),
                PASSWORD);
    }

    public void testEcho(String message) throws InterruptedException, ExecutionException, ClientConnectionException,
            IllegalAccessException {
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
        client.setClientPolicy(
                new StompClientPolicy(180000, 0, 1000000, DEFAULT_REQUEST_QUEUE, DEFAULT_RESPONSE_QUEUE));
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

        List<Certificate> peerCertificates = client.getPeerCertificates();
        assertNotNull(peerCertificates);
        assertFalse(peerCertificates.isEmpty());

        client.close();
        listener.close();

        assertNotNull(response);
        assertEquals(message, new String(response, UTF8));
    }

}
