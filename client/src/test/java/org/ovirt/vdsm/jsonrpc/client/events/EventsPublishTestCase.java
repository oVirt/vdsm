package org.ovirt.vdsm.jsonrpc.client.events;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;
import static org.ovirt.vdsm.jsonrpc.client.events.EventTestUtls.createPublisher;

import java.io.IOException;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.Before;
import org.junit.Test;
import org.reactivestreams.Subscription;

public class EventsPublishTestCase {
    private AtomicInteger counter = new AtomicInteger();
    private EventPublisher publisher;
    private Subscription subscription;
    private int value;

    @Before
    public void setup() {
        this.publisher = createPublisher();
    }

    private void subscribe(String subscriptionId) {
        EventSubscriber subscriber = new EventSubscriber(subscriptionId) {

            @Override
            public void onSubscribe(Subscription sub) {
                subscription = sub;
                subscription.request(10);
            }

            @Override
            public void onNext(Map<String, Object> map) {
                counter.incrementAndGet();
                value = (int) map.get("value");
            }

            @Override
            public void onError(Throwable t) {}

            @Override
            public void onComplete() {}
        };
        this.publisher.subscribe(subscriber);
    }

    private void unsubscribe() {
        this.subscription.cancel();
    }

    @Test
    public void testPublish() {
        subscribe("*|*|*|update");

        Map<String, Object> params = new HashMap<String, Object>();
        params.put("value", 42);
        try {
            publisher.publish("|testcase|test|update", params);
            sleepForEvent(5000);
            assertEquals(counter.get(), 1);
            assertEquals(value, 42);
        } catch (IOException ioe) {
            fail();
        }
        unsubscribe();
    }

    public void sleepForEvent(int timeout) {
        try {
            TimeUnit.MILLISECONDS.sleep(timeout);
        } catch (InterruptedException ignored) {
        }
    }
}
