package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.junit.Ignore;
import org.junit.Test;


// Requires rabbit with stomp queue configured
@Ignore
public class RabbitIntegrationTest {

    private final static String HOSTNAME = "localhost";
    private final static int PORT = 61613;

    @Test
    public void testSubscription() throws IOException, InterruptedException {
        StompClient client = new StompClient(HOSTNAME, PORT);
        final CountDownLatch updated = new CountDownLatch(1);

        StompClient subClient = new StompClient(HOSTNAME, PORT);
        client.subscribe("/queue/stomp", new Listener() {
            
            @Override
            public void update(String content) {
                assertEquals("Hello World!", content);
                updated.countDown();
            }
            
            @Override
            public void error(Map<String, String> error) {
                fail();
            }
        });
        client.send("Hello World!", "/queue/stomp");
        updated.await(5, TimeUnit.SECONDS);
        assertEquals(0, updated.getCount());
        client.disconnect();
        client.stop();
        subClient.disconnect();
        subClient.stop();
    }

    @Test
    public void testTransactions() throws IOException, InterruptedException {
        final CountDownLatch second = new CountDownLatch(4);
        final CountDownLatch first = new CountDownLatch(1);
        Listener listener = new Listener() {

            @Override
            public void update(String message) {
                assertEquals("Hello World!", message);
                second.countDown();
                first.countDown();
            }

            @Override
            public void error(Map<String, String> error) {
                fail();
            }
        };
        StompClient clientSubscriber1 = new StompClient(HOSTNAME, PORT);
        clientSubscriber1.subscribe("/queue/stomp", listener);


        StompClient clientSender = new StompClient(HOSTNAME, PORT);
        clientSender.begin();
        clientSender.send("Hello World!", "/queue/stomp");
        assertEquals(4, second.getCount());
        assertEquals(1, first.getCount());
        clientSender.commit();
        first.await(1, TimeUnit.SECONDS);
        assertEquals(0, first.getCount());

        clientSender.begin();
        clientSender.send("Hello World!", "/queue/stomp");
        clientSender.send("Hello World!", "/queue/stomp");
        clientSender.send("Hello World!", "/queue/stomp");
        assertEquals(3, second.getCount());
        clientSender.commit();

        second.await(1, TimeUnit.SECONDS);
        assertEquals(0, second.getCount());
        clientSubscriber1.unsubscribe("/queue/stomp");
        clientSubscriber1.disconnect();
        clientSubscriber1.stop();

        clientSender.disconnect();
    }
}
