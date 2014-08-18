package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.junit.Test;

public class StompInternalTest {

    private final static String HOSTNAME = "localhost";
    private final static int TIMEOUT = 10;

    @Test
    public void testConnection() throws IOException, InterruptedException {
        final CountDownLatch messages = new CountDownLatch(1);
        StompServer server = new StompServer(HOSTNAME, 0);
        StompClient clientSubscriber = new StompClient(HOSTNAME, server.getPort());
        clientSubscriber.subscribe("/queue/a", new Listener() {

            @Override
            public void update(String message) {
                messages.countDown();
            }

            @Override
            public void error(Map<String, String> error) {
                fail();
            }
        });
        StompClient clientSender = new StompClient(HOSTNAME, server.getPort());
        clientSender.send("Hello World!", "/queue/a");
        messages.await(TIMEOUT, TimeUnit.SECONDS);
        assertEquals(0, messages.getCount());

        clientSubscriber.unsubscribe("/queue/a");
        clientSubscriber.disconnect();
        clientSubscriber.stop();

        clientSender.disconnect();
        clientSender.stop();
        server.stop();
    }

    @Test
    public void testMultipeSubs() throws IOException, InterruptedException {
        final CountDownLatch messages = new CountDownLatch(3);
        Listener listener = new Listener() {

            @Override
            public void update(String message) {
                messages.countDown();
            }

            @Override
            public void error(Map<String, String> error) {
                fail();
            }
        };
        StompServer server = new StompServer(HOSTNAME, 0);
        StompClient clientSubscriber1 = new StompClient(HOSTNAME, server.getPort());
        StompClient clientSubscriber2 = new StompClient(HOSTNAME, server.getPort());
        StompClient clientSubscriber3 = new StompClient(HOSTNAME, server.getPort());
        clientSubscriber1.subscribe("/queue/a", listener);
        clientSubscriber2.subscribe("/queue/a", listener);
        clientSubscriber3.subscribe("/queue/a", listener);

        StompClient clientSender = new StompClient(HOSTNAME, server.getPort());
        clientSender.send("Hello World!", "/queue/a");
        messages.await(TIMEOUT, TimeUnit.SECONDS);
        assertEquals(0, messages.getCount());

        clientSubscriber1.unsubscribe("/queue/a");
        clientSubscriber2.unsubscribe("/queue/a");
        clientSubscriber3.unsubscribe("/queue/a");
        clientSubscriber1.disconnect();
        clientSubscriber2.disconnect();
        clientSubscriber3.disconnect();
        clientSubscriber1.stop();
        clientSubscriber2.stop();
        clientSubscriber3.stop();

        clientSender.disconnect();
        server.stop();
    }

    @Test
    public void testTransactionCommit() throws IOException, InterruptedException {
        final CountDownLatch messages = new CountDownLatch(3);
        Listener listener = new Listener() {

            @Override
            public void update(String message) {
                messages.countDown();
            }

            @Override
            public void error(Map<String, String> error) {
                fail();
            }
        };
        StompServer server = new StompServer(HOSTNAME, 0);
        StompClient clientSubscriber1 = new StompClient(HOSTNAME, server.getPort());
        StompClient clientSubscriber2 = new StompClient(HOSTNAME, server.getPort());
        StompClient clientSubscriber3 = new StompClient(HOSTNAME, server.getPort());
        clientSubscriber1.subscribe("/queue/a", listener);
        clientSubscriber2.subscribe("/queue/b", listener);
        clientSubscriber3.subscribe("/queue/c", listener);

        StompClient clientSender = new StompClient(HOSTNAME, server.getPort());
        clientSender.begin();
        clientSender.send("Hello World!", "/queue/a");
        clientSender.send("Hello World!", "/queue/b");
        clientSender.send("Hello World!", "/queue/c");
        clientSender.commit();
        messages.await(TIMEOUT, TimeUnit.SECONDS);

        assertEquals(0, messages.getCount());
        clientSubscriber1.unsubscribe("/queue/a");
        clientSubscriber2.unsubscribe("/queue/b");
        clientSubscriber3.unsubscribe("/queue/c");
        clientSubscriber1.disconnect();
        clientSubscriber2.disconnect();
        clientSubscriber3.disconnect();
        clientSubscriber1.stop();
        clientSubscriber2.stop();
        clientSubscriber3.stop();

        clientSender.disconnect();
        server.stop();
    }
}
