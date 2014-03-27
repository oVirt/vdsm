package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import static org.junit.Assert.assertEquals;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.junit.Test;

public class StompInternalTest {

    private final static String HOSTNAME = "localhost";
    private final static int PORT = 61624;

    @Test
    public void testConnection() throws IOException, InterruptedException {
        final CountDownLatch messages = new CountDownLatch(1);
        StompServer server = new StompServer(HOSTNAME, PORT);
        StompClient clientSubscriber = new StompClient(HOSTNAME, PORT);
        clientSubscriber.subscribe("/queue/a", new Listener() {

            @Override
            public void update(String message) {
                messages.countDown();
            }

            @Override
            public void error(Map<String, String> error) {
            }
        });
        StompClient clientSender = new StompClient(HOSTNAME, PORT);
        clientSender.send("Hello World!", "/queue/a");
        messages.await(1, TimeUnit.SECONDS);
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
            }
        };
        StompServer server = new StompServer(HOSTNAME, PORT -1);
        StompClient clientSubscriber1 = new StompClient(HOSTNAME, PORT - 1);
        StompClient clientSubscriber2 = new StompClient(HOSTNAME, PORT - 1);
        StompClient clientSubscriber3 = new StompClient(HOSTNAME, PORT - 1);
        clientSubscriber1.subscribe("/queue/a", listener);
        clientSubscriber2.subscribe("/queue/a", listener);
        clientSubscriber3.subscribe("/queue/a", listener);

        StompClient clientSender = new StompClient(HOSTNAME, PORT - 1);
        clientSender.send("Hello World!", "/queue/a");
        messages.await(1, TimeUnit.SECONDS);
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
            }
        };
        StompServer server = new StompServer(HOSTNAME, PORT - 2);
        StompClient clientSubscriber1 = new StompClient(HOSTNAME, PORT - 2);
        StompClient clientSubscriber2 = new StompClient(HOSTNAME, PORT - 2);
        StompClient clientSubscriber3 = new StompClient(HOSTNAME, PORT - 2);
        clientSubscriber1.subscribe("/queue/a", listener);
        clientSubscriber2.subscribe("/queue/b", listener);
        clientSubscriber3.subscribe("/queue/c", listener);

        StompClient clientSender = new StompClient(HOSTNAME, PORT - 2);
        clientSender.begin();
        clientSender.send("Hello World!", "/queue/a");
        clientSender.send("Hello World!", "/queue/b");
        clientSender.send("Hello World!", "/queue/c");
        clientSender.commit();
        messages.await(3, TimeUnit.SECONDS);

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
