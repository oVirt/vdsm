package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;

import java.io.IOException;
import java.security.GeneralSecurityException;
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
    private final static int TIMEOUT_SEC = 6;
    private final static String HOSTNAME = "localhost";
    private final static int PORT = 61625;
    private final static String MESSAGE = "Hello world!";
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
    public void testEcho() throws InterruptedException, ExecutionException, ClientConnectionException {
        final BlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(5);
        Future<ReactorListener> futureListener =
                this.listeningReactor.createListener(HOSTNAME, PORT, new EventListener() {

                    @Override
                    public void onAcccept(final ReactorClient client) {
                        client.addEventListener(new MessageListener() {
                            @Override
                            public void onMessageReceived(byte[] message) {
                                client.sendMessage(message);
                            }
                        });
                    }
                });

        ReactorListener listener = futureListener.get();
        assertNotNull(listener);

        ReactorClient client = this.sendingReactor.createClient(HOSTNAME, PORT);
        client.addEventListener(new ReactorClient.MessageListener() {

            @Override
            public void onMessageReceived(byte[] message) {
                queue.add(message);
            }
        });
        client.connect();

        client.sendMessage(MESSAGE.getBytes());
        byte[] message = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);

        client.sendMessage(MESSAGE.getBytes());
        message = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);

        client.close();
        listener.close();

        assertNotNull(message);
        assertEquals(MESSAGE, new String(message, UTF8));
    }

}
