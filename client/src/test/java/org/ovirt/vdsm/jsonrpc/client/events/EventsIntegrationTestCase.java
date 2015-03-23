package org.ovirt.vdsm.jsonrpc.client.events;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;
import static org.ovirt.vdsm.jsonrpc.client.events.EventTestUtls.MESSAGE_CONTENT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompCommonClient.DEFAULT_REQUEST_QUEUE;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompCommonClient.DEFAULT_RESPONSE_QUEUE;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.internal.ResponseWorker;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorFactory;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener.EventListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorType;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompClientPolicy;
import org.reactivestreams.Subscription;

public class EventsIntegrationTestCase {

    private final static String HOSTNAME = "localhost";
    private final static int PORT = 0;
    private final static int LIMIT = 10;

    private Reactor sendingReactor;
    private Reactor listeningReactor;

    private ReactorClient listeningClient = null;

    private int counter = 0;
    private boolean completed = false;

    @Before
    public void setUp() throws IOException, ClientConnectionException {
        this.listeningReactor = ReactorFactory.getReactor(null, ReactorType.STOMP);
        this.sendingReactor = ReactorFactory.getReactor(null, ReactorType.STOMP);
    }

    @After
    public void tearDown() throws IOException {
        this.sendingReactor.close();
        this.listeningReactor.close();
    }

    @Test
    public void testEvents() throws ClientConnectionException, InterruptedException, ExecutionException {
        Future<ReactorListener> futureListener =
                this.listeningReactor.createListener(HOSTNAME, PORT, new EventListener() {

                    @Override
                    public void onAcccept(final ReactorClient client) {
                        listeningClient = client;
                    }
                });

        ReactorListener listener = futureListener.get();

        ReactorClient client = this.sendingReactor.createClient(HOSTNAME, listener.getPort());
        client.setClientPolicy(new StompClientPolicy(180000, 0, 1000000, DEFAULT_REQUEST_QUEUE, DEFAULT_RESPONSE_QUEUE));

        ResponseWorker worker = ReactorFactory.getWorker(Runtime.getRuntime().availableProcessors());
        worker.register(client);
        client.connect();

        EventPublisher publisher = worker.getPublisher();
        EventSubscriber subscriber = new EventSubscriber("*|*|*|update") {

            private Subscription subscription;

            @Override
            public void onSubscribe(Subscription subscription) {
                this.subscription = subscription;
                this.subscription.request(1);
            }

            @Override
            public void onNext(Map<String, Object> map) {
                if (map == null || map.isEmpty()) {
                    fail();
                }
                if (map.get("value").equals(new Integer(42))) {
                    counter++;
                }
                if (counter == LIMIT) {
                    this.subscription.cancel();
                }
                this.subscription.request(1);
            }

            @Override
            public void onError(Throwable t) {
            }

            @Override
            public void onComplete() {
                completed = true;
            }
        };
        publisher.subscribe(subscriber);

        Thread generator = new Thread(new EventGenerator(this.listeningClient));
        generator.start();
        generator.join();

        // make sure events are delivered
        TimeUnit.MILLISECONDS.sleep(500);

        assertEquals(LIMIT, counter);
        assertTrue(completed);
    }

    class EventGenerator implements Runnable {
        private final static long TIMEOUT = 50;
        private ReactorClient client;
        private int counter;
        private boolean isRunning = true;

        public EventGenerator(ReactorClient client) {
            this.client = client;
            this.counter = 0;
        }

        @Override
        public void run() {
            while (this.isRunning) {
                if (this.counter == LIMIT) {
                    this.stop();
                }

                try {
                    this.client.sendMessage(MESSAGE_CONTENT.getBytes());
                    TimeUnit.MILLISECONDS.sleep(TIMEOUT);
                } catch (ClientConnectionException | InterruptedException e) {
                    this.stop();
                    fail();
                }
                this.counter++;
            }
        }

        private void stop() {
            this.isRunning = false;
        }

    }

}
