package org.ovirt.vdsm.jsonrpc.client.events;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.timeout;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.ovirt.vdsm.jsonrpc.client.events.EventTestUtls.createPublisher;

import java.lang.reflect.Field;
import java.util.HashMap;
import java.util.Map;

import org.junit.Test;
import org.mockito.ArgumentCaptor;
import org.ovirt.vdsm.jsonrpc.client.EventDecomposer;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcEvent;
import org.reactivestreams.Subscription;

public class PublisherTestCase {

    @Test
    public void testSingleMsg() throws NoSuchFieldException, SecurityException, IllegalArgumentException,
            IllegalAccessException {
        EventPublisher publisher = createPublisher();

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("local|testcase|test|uuid");

        EventDecomposer decomposer = mock(EventDecomposer.class);
        Map<String, Object> map = new HashMap<>();
        when(decomposer.decompose(event)).thenReturn(map);
        setField(publisher, "decomposer", decomposer);

        EventSubscriber subscriber = mock(EventSubscriber.class);
        when(subscriber.getSubscriptionId()).thenReturn("*|*|*|uuid");
        ArgumentCaptor<Subscription> captor = ArgumentCaptor.forClass(Subscription.class);

        publisher.subscribe(subscriber);
        verify(subscriber).onSubscribe(captor.capture());

        Subscription subscription = captor.getValue();

        publisher.process(event);
        verify(subscriber, timeout(500).times(0)).onNext(map);

        subscription.request(1);
        verify(subscriber, timeout(500).times(1)).onNext(map);

        subscription.cancel();
        verify(subscriber, timeout(500).times(1)).onComplete();
    }

    @Test
    public void testMultipleMsg() throws NoSuchFieldException, SecurityException, IllegalArgumentException,
            IllegalAccessException {
        EventPublisher publisher = createPublisher();

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("local|testcase|test|uuid");

        EventDecomposer decomposer = mock(EventDecomposer.class);
        Map<String, Object> map = new HashMap<>();
        when(decomposer.decompose(event)).thenReturn(map);
        setField(publisher, "decomposer", decomposer);

        EventSubscriber subscriber = mock(EventSubscriber.class);
        when(subscriber.getSubscriptionId()).thenReturn("*|*|test|*");
        ArgumentCaptor<Subscription> captor = ArgumentCaptor.forClass(Subscription.class);

        publisher.subscribe(subscriber);
        verify(subscriber).onSubscribe(captor.capture());

        Subscription subscription = captor.getValue();

        subscription.request(10);

        for (int i = 0; i < 15; i++) {
            publisher.process(event);
        }

        verify(subscriber, timeout(1000).times(10)).onNext(map);
    }

    @Test
    public void testCancelledSubscription() throws NoSuchFieldException, SecurityException, IllegalArgumentException,
            IllegalAccessException {
        EventPublisher publisher = createPublisher();

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("local|testcase|test|uuid");

        EventDecomposer decomposer = mock(EventDecomposer.class);
        Map<String, Object> map = new HashMap<>();
        when(decomposer.decompose(event)).thenReturn(map);
        setField(publisher, "decomposer", decomposer);

        EventSubscriber subscriber = mock(EventSubscriber.class);
        when(subscriber.getSubscriptionId()).thenReturn("*|*|test|*");
        ArgumentCaptor<Subscription> captor = ArgumentCaptor.forClass(Subscription.class);

        publisher.subscribe(subscriber);
        verify(subscriber).onSubscribe(captor.capture());

        Subscription subscription = captor.getValue();
        subscription.cancel();

        publisher.process(event);

        verify(subscriber, never()).onNext(map);
        verify(subscriber, times(1)).onComplete();
    }

    public static void setField(Object obj, String fieldName, Object value) throws NoSuchFieldException,
            SecurityException, IllegalArgumentException, IllegalAccessException {
        Field f = obj.getClass().getDeclaredField(fieldName);
        f.setAccessible(true);
        f.set(obj, value);
    }

}
