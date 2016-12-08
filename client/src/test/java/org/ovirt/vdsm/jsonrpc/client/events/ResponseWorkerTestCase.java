package org.ovirt.vdsm.jsonrpc.client.events;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.buildErrorResponse;

import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.mockito.ArgumentCaptor;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcResponse;
import org.ovirt.vdsm.jsonrpc.client.internal.ResponseWorker;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient.MessageListener;
import org.reactivestreams.Subscription;

public class ResponseWorkerTestCase {

    private ResponseWorker worker;

    @Before
    public void setUp() {
        this.worker = new ResponseWorker(Runtime.getRuntime().availableProcessors());
    }

    @After
    public void tearDown() {
        this.worker.close();
        this.worker = null;
    }

    @Test
    public void testIncomingMessage() throws InterruptedException {
        final String message =
                "{\"params\": {\"82318566-6bf4-4381-8682-d672d88350bb\": \"Up\"}, \"jsonrpc\": \"2.0\", \"method\":"
                + " \"|virt|VM_status|82318566-6bf4-4381-8682-d672d88350bb\"}";
        final CountDownLatch latch = new CountDownLatch(1);

        EventSubscriber subscriber = new EventSubscriber("*|virt|VM_status|*") {

            private Subscription subscription;

            @Override
            public void onSubscribe(Subscription s) {
                this.subscription = s;
                this.subscription.request(1);
            }

            @Override
            public void onNext(Map<String, Object> map) {
                assertEquals("Up", map.get("82318566-6bf4-4381-8682-d672d88350bb"));
                this.subscription.cancel();
                latch.countDown();
            }

            @Override
            public void onError(Throwable t) {
                fail();
            }

            @Override
            public void onComplete() {
            }
        };
        processMessage(subscriber, message.getBytes(UTF8), latch);
    }

    @Test
    public void testNetworkIssue() throws InterruptedException {
        JsonRpcResponse response = buildErrorResponse(null, "localhost:809653068", "Heartbeat exeeded");
        final CountDownLatch latch = new CountDownLatch(1);

        EventSubscriber subscriber = new EventSubscriber("localhost|virt|VM_status|*") {

            private Subscription subscription;

            @Override
            public void onSubscribe(Subscription s) {
                this.subscription = s;
                this.subscription.request(1);
            }

            @Override
            public void onNext(Map<String, Object> t) {
                fail();
            }

            @Override
            public void onError(Throwable t) {
                assertTrue(ClientConnectionException.class.isInstance(t));
                assertEquals("Heartbeat exeeded", t.getMessage());
                latch.countDown();
            }

            @Override
            public void onComplete() {
            }
        };

        processMessage(subscriber, response.toByteArray(), latch);
    }

    private void processMessage(EventSubscriber subscriber, byte[] message, CountDownLatch waitingLatch)
            throws InterruptedException {
        ReactorClient client = mock(ReactorClient.class);
        ArgumentCaptor<MessageListener> argument = ArgumentCaptor.forClass(MessageListener.class);

        this.worker.register(client);

        verify(client).addEventListener(argument.capture());
        MessageListener listener = argument.getValue();
        this.worker.getPublisher().subscribe(subscriber);
        listener.onMessageReceived(message);
        waitingLatch.await(1, TimeUnit.SECONDS);
        assertEquals(0, waitingLatch.getCount());
    }

}
