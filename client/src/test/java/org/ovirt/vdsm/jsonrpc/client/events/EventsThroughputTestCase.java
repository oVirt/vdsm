package org.ovirt.vdsm.jsonrpc.client.events;

import static org.ovirt.vdsm.jsonrpc.client.events.EventTestUtls.MESSAGE_CONTENT;
import static org.ovirt.vdsm.jsonrpc.client.events.EventTestUtls.createPublisher;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.Before;
import org.junit.Ignore;
import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcEvent;
import org.reactivestreams.Subscription;

//Remove when measuring throughput
@Ignore
public class EventsThroughputTestCase {
    private static final JsonRpcEvent EVENT = JsonRpcEvent.fromByteArray(MESSAGE_CONTENT.getBytes());
    private static final int TIMEOUT = 10000;
    private static final int TIMES = 10;
    private AtomicInteger counter = new AtomicInteger();
    private EventPublisher publisher;
    private List<Integer> result = new ArrayList<>();
    private Subscription subscription;

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
                subscription.request(1);
            }

            @Override
            public void onError(Throwable t) {
            }

            @Override
            public void onComplete() {
            }
        };
        this.publisher.subscribe(subscriber);
    }

    private void unsubscribe() {
        this.subscription.cancel();
        this.counter.set(0);
    }

    @Test
    public void testThroughput() {
        for (int i = 0; i < TIMES; i++) {
            subscribe("*|*|*|update");
            EventGenerator gen = new EventGenerator();
            Thread generator = new Thread(gen);
            generator.start();
            gen.stop(TIMEOUT);
            unsubscribe();
        }
        System.out.println("Min value " + Collections.min(this.result));
        System.out.println("Max value " + Collections.max(this.result));
        System.out.println("Avg value " + average(this.result));
    }

    private int average(List<Integer> results) {
        int sum = 0;
        for (Integer result : results) {
            sum += result;
        }
        return sum / results.size();
    }

    class EventGenerator implements Runnable {

        private volatile boolean isRunning = true;

        @Override
        public void run() {
            long start = System.currentTimeMillis();
            for (int i = 0; i < Integer.MAX_VALUE; i++) {
                if (!this.isRunning) {
                    break;
                }
                publisher.process(EVENT);
            }
            int time = (int) (System.currentTimeMillis() - start) / 1000;
            int value = counter.get() / time;
            result.add(value);
        }

        public void stop(int timeout) {
            try {
                TimeUnit.MILLISECONDS.sleep(timeout);
            } catch (InterruptedException ignored) {
            } finally {
                this.isRunning = false;
            }
        }
    }
}
