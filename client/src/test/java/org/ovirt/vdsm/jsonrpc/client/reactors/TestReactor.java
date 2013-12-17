package org.ovirt.vdsm.jsonrpc.client.reactors;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import java.io.IOException;
import java.net.UnknownHostException;
import java.nio.ByteBuffer;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import org.junit.After;
import org.junit.Before;
import org.junit.Ignore;
import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.NioReactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient.EventListener;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryPolicy;

// This class is heavily time dependent so there is
// good number of timeouts. It is ignored due to time
// needed to run it.
@Ignore
public class TestReactor {

    private final static int TIMEOUT_SEC = 6;
    private final static String HOSTNAME = "127.0.0.1";
    private final static String DATA = "Hello World!";
    private NioReactor reactorForListener;
    private NioReactor reactorForClient;

    @Before
    public void setUp() throws Exception {
        this.reactorForListener = new NioReactor();
        this.reactorForClient = new NioReactor();
    }

    @After
    public void tearDown() throws Exception {
        this.reactorForListener.close();
        this.reactorForClient.close();
    }

    @Test
    public void testConnectionBetweenListenerAndClient() throws UnknownHostException, InterruptedException,
            ExecutionException, TimeoutException,
            ClientConnectionException {
        final BlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(1);
        final Future<ReactorListener> futureListener = this.reactorForListener.createListener(HOSTNAME, 6669,
                new ReactorListener.EventListener() {
                    @Override
                    public void onAcccept(ReactorListener listener, final ReactorClient client) {
                        client.addEventListener(new EventListener() {
                            @Override
                            public void onMessageReceived(byte[] message) {
                                client.sendMessage(message);
                            }
                        });
                    }
                });

        ReactorListener listener = futureListener.get(TIMEOUT_SEC, TimeUnit.SECONDS);
        assertNotNull(listener);
        assertTrue(futureListener.isDone());

        ReactorClient client = this.reactorForClient.createClient(HOSTNAME, 6669);
        assertNotNull(client);

        client.addEventListener(new EventListener() {
            @Override
            public void onMessageReceived(byte[] message) {
                queue.add(message);
            }
        });

        final ByteBuffer buff = ByteBuffer.allocate(DATA.length());
        buff.put(DATA.getBytes());
        buff.position(0);
        client.connect();
        client.sendMessage(buff.array());
        byte[] message = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);
        assertNotNull(message);
        assertArrayEquals(buff.array(), message);

        client.close();
        listener.close();
    }

    @Test
    public void testRetryConnectionBetweenListenerAndClient() throws InterruptedException, ExecutionException {
        final BlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(1);
        final ExecutorService executorService = Executors.newCachedThreadPool();

        final Callable<ReactorClient> clientTask = new Callable<ReactorClient>() {

            @Override
            public ReactorClient call() throws Exception {
                ReactorClient client = reactorForClient.createClient(HOSTNAME, 6668);

                client.addEventListener(new EventListener() {
                    @Override
                    public void onMessageReceived(byte[] message) {
                        queue.add(message);
                    }
                });
                client.setRetryPolicy(new RetryPolicy(2000, 10, IOException.class));
                client.connect();
                return client;
            }
        };

        final Callable<ReactorListener> listenerTask = new Callable<ReactorListener>() {

            @Override
            public ReactorListener call() throws Exception {
                final Future<ReactorListener> futureListener = reactorForListener.createListener(HOSTNAME, 6668,
                        new ReactorListener.EventListener() {
                            @Override
                            public void onAcccept(ReactorListener listener, final ReactorClient client) {
                                client.addEventListener(new EventListener() {
                                    @Override
                                    public void onMessageReceived(byte[] message) {
                                        client.sendMessage(message);
                                    }
                                });
                            }
                        });

                return futureListener.get(TIMEOUT_SEC, TimeUnit.SECONDS);
            }
        };
        Future<ReactorClient> clientFuture = executorService.submit(clientTask);
        Thread.sleep(2000);
        Future<ReactorListener> listenerFuture = executorService.submit(listenerTask);

        ReactorListener listener = listenerFuture.get();
        assertTrue(listenerFuture.isDone());
        assertNotNull(listener);

        ReactorClient client = clientFuture.get();
        assertTrue(clientFuture.isDone());
        assertNotNull(client);

        final ByteBuffer buff = ByteBuffer.allocate(DATA.length());
        buff.put(DATA.getBytes());
        buff.position(0);
        client.sendMessage(buff.array());
        byte[] message = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);
        assertNotNull(message);
        assertArrayEquals(buff.array(), message);

        client.close();
        listener.close();
    }

    @Test
    public void testNotConnectedRetry() throws InterruptedException, TimeoutException, ClientConnectionException,
            ExecutionException, IOException {
        final BlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(1);
        Future<ReactorListener> futureListener = this.reactorForListener.createListener(HOSTNAME, 6667,
                new ReactorListener.EventListener() {
                    @Override
                    public void onAcccept(ReactorListener listener, final ReactorClient client) {
                        client.addEventListener(new EventListener() {
                            @Override
                            public void onMessageReceived(byte[] message) {
                                client.sendMessage(message);
                            }
                        });
                    }
                });

        ReactorListener listener = futureListener.get(TIMEOUT_SEC, TimeUnit.SECONDS);
        assertNotNull(listener);
        assertTrue(futureListener.isDone());

        ReactorClient client = this.reactorForClient.createClient(HOSTNAME, 6667);
        assertNotNull(client);

        client.addEventListener(new EventListener() {
            @Override
            public void onMessageReceived(byte[] message) {
                queue.add(message);
            }
        });
        client.connect();
        listener.close();

        futureListener = this.reactorForListener.createListener(HOSTNAME, 6667,
                new ReactorListener.EventListener() {
                    @Override
                    public void onAcccept(ReactorListener listener, final ReactorClient client) {
                        client.addEventListener(new EventListener() {
                            @Override
                            public void onMessageReceived(byte[] message) {
                                client.sendMessage(message);
                            }
                        });
                    }
                });

        listener = futureListener.get(TIMEOUT_SEC, TimeUnit.SECONDS);

        final ByteBuffer buff = ByteBuffer.allocate(DATA.length());
        buff.put(DATA.getBytes());
        buff.position(0);

        client.sendMessage(buff.array());
        byte[] message = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);

        assertNotNull(message);
        assertArrayEquals(buff.array(), message);
    }
}
