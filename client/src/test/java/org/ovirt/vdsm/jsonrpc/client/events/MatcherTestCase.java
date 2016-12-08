package org.ovirt.vdsm.jsonrpc.client.events;

import static org.junit.Assert.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.parse;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcEvent;

public class MatcherTestCase {

    @Test(expected = IllegalArgumentException.class)
    public void testAllSubscription() {
        SubscriptionHolder holder = mock(SubscriptionHolder.class);
        when(holder.getId()).thenReturn("*|*|*|*");

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
    }

    @Test(expected = IllegalArgumentException.class)
    public void testEmptyKeySubscription() {
        String id = "|*|*|*";
        SubscriptionHolder holder = mock(SubscriptionHolder.class);
        when(holder.getId()).thenReturn(id);
        when(holder.getParsedId()).thenReturn(parse(id));

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
    }

    @Test
    public void testUidLevelSubscription() {
        String id = "*|*|*|uuid";
        SubscriptionHolder holder = mock(SubscriptionHolder.class);
        when(holder.getId()).thenReturn(id);
        when(holder.getParsedId()).thenReturn(parse(id));
        SubscriptionHolder differentHolder = mock(SubscriptionHolder.class);
        when(differentHolder.getId()).thenReturn("*|*|*|uuid2");

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
        matcher.add(differentHolder);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("localhost|testcase|test|uuid");
        JsonRpcEvent secondEvent = mock(JsonRpcEvent.class);
        when(secondEvent.getMethod()).thenReturn("localhost|testcase|test|uuid2");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(1, holders.size());

        holders = matcher.match(secondEvent);
        assertEquals(1, holders.size());

        matcher.remove(holder);
        holders = matcher.match(event);
        assertEquals(0, holders.size());
    }

    @Test
    public void testOperationSubscription() {
        SubscriptionHolder holder = mock(SubscriptionHolder.class);
        when(holder.getId()).thenReturn("*|*|test|*");
        when(holder.getFilteredId()).thenReturn(new ArrayList<String>(Arrays.asList("test")));

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("localhost|testcase|test|uuid");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(1, holders.size());
    }

    @Test
    public void testUidAndOperationSubscription() {
        SubscriptionHolder holder = mock(SubscriptionHolder.class);
        when(holder.getId()).thenReturn("*|*|test|uuid");
        when(holder.getFilteredId()).thenReturn(new ArrayList<String>(Arrays.asList("test", "uuid")));

        SubscriptionHolder differentHolder = mock(SubscriptionHolder.class);
        when(differentHolder.getId()).thenReturn("*|*|test|*");
        when(differentHolder.getFilteredId()).thenReturn(new ArrayList<String>(Arrays.asList("test")));

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
        matcher.add(differentHolder);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("localhost|testcase|test|uuid");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(2, holders.size());
    }

    @Test
    public void testUidAndOperationAndComponentSubscription() {
        SubscriptionHolder holder = mock(SubscriptionHolder.class);
        when(holder.getId()).thenReturn("*|*|test|uuid");
        when(holder.getFilteredId()).thenReturn(new ArrayList<String>(Arrays.asList("test", "uuid")));

        SubscriptionHolder differentHolder = mock(SubscriptionHolder.class);
        when(differentHolder.getId()).thenReturn("*|*|test|*");
        when(differentHolder.getFilteredId()).thenReturn(new ArrayList<String>(Arrays.asList("test")));

        SubscriptionHolder thirdHolder = mock(SubscriptionHolder.class);
        when(thirdHolder.getId()).thenReturn("*|testcase|*|*");
        when(thirdHolder.getFilteredId()).thenReturn(new ArrayList<String>(Arrays.asList("testcase")));

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
        matcher.add(differentHolder);
        matcher.add(thirdHolder);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("localhost|testcase|test|uuid");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(3, holders.size());
    }

    @Test
    public void testOperationAndComponentSubscription() {
        SubscriptionHolder holder = mock(SubscriptionHolder.class);
        when(holder.getId()).thenReturn("*|*|*|uuid");
        when(holder.getFilteredId()).thenReturn(new ArrayList<String>(Arrays.asList("uuid")));

        SubscriptionHolder differentHolder = mock(SubscriptionHolder.class);
        when(differentHolder.getId()).thenReturn("*|*|test|*");
        when(differentHolder.getFilteredId()).thenReturn(new ArrayList<String>(Arrays.asList("test")));

        SubscriptionHolder thirdHolder = mock(SubscriptionHolder.class);
        when(thirdHolder.getId()).thenReturn("*|testcase|test2|*");
        when(thirdHolder.getFilteredId()).thenReturn(new ArrayList<String>(Arrays.asList("testcase", "test2")));

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
        matcher.add(differentHolder);
        matcher.add(thirdHolder);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("localhost|testcase|test|uuid");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(2, holders.size());

        event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("localhost|testcase|test2|uuid");

        holders = matcher.match(event);
        assertEquals(2, holders.size());
    }

    @Test
    public void testMixedSubscription() {
        EventSubscriber subscriber = mock(EventSubscriber.class);
        when(subscriber.getSubscriptionId()).thenReturn("*|testcase|*|uuid");
        SubscriptionHolder holder = new SubscriptionHolder(subscriber, new AtomicInteger());

        EventSubscriber differentSubscriber = mock(EventSubscriber.class);
        when(differentSubscriber.getSubscriptionId()).thenReturn("*|testcase|test|*");
        SubscriptionHolder differentHolder = new SubscriptionHolder(differentSubscriber, new AtomicInteger());

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
        matcher.add(differentHolder);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("localhost|testcase|test2|uuid");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(1, holders.size());
    }

    @Test
    public void testReceiverSubscription() {
        EventSubscriber subscriber = mock(EventSubscriber.class);
        when(subscriber.getSubscriptionId()).thenReturn("localhost|testcase|*|uuid");
        SubscriptionHolder holder = new SubscriptionHolder(subscriber, new AtomicInteger());

        EventSubscriber differentSubscriber = mock(EventSubscriber.class);
        when(differentSubscriber.getSubscriptionId()).thenReturn("remote|*|test|*");
        SubscriptionHolder differentHolder = new SubscriptionHolder(differentSubscriber, new AtomicInteger());

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
        matcher.add(differentHolder);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("remote|testcase|test|uuid2");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(1, holders.size());

        event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("localhost|testcase|test|uuid");

        holders = matcher.match(event);
        assertEquals(1, holders.size());
    }

    @Test
    public void testReceiverWithComponentAndOperationSubscription() {
        EventSubscriber subscriber = mock(EventSubscriber.class);
        when(subscriber.getSubscriptionId()).thenReturn("localhost|*|VM_status|*");
        SubscriptionHolder holder = new SubscriptionHolder(subscriber, new AtomicInteger());

        EventSubscriber differentSubscriber = mock(EventSubscriber.class);
        when(differentSubscriber.getSubscriptionId()).thenReturn("remote|*|VM_status|*");
        SubscriptionHolder differentHolder = new SubscriptionHolder(differentSubscriber, new AtomicInteger());

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
        matcher.add(differentHolder);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("remote|virt|VM_status|uuid");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(1, holders.size());

        event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("remote|virt|VM_status|uuid");

        holders = matcher.match(event);
        assertEquals(1, holders.size());
    }

    @Test
    public void testReceiverOnlySubscription() {
        EventSubscriber subscriber = mock(EventSubscriber.class);
        when(subscriber.getSubscriptionId()).thenReturn("localhost|*|VM.list|*");
        SubscriptionHolder holder = new SubscriptionHolder(subscriber, new AtomicInteger());

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("localhost|*|*|*");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(1, holders.size());
    }

    @Test
    public void testVmMigrationSubscription() {
        EventSubscriber subscriber = mock(EventSubscriber.class);
        when(subscriber.getSubscriptionId()).thenReturn("*|*|VM_migration_status|*");
        SubscriptionHolder holder = new SubscriptionHolder(subscriber, new AtomicInteger());

        EventSubscriber subscriber2 = mock(EventSubscriber.class);
        when(subscriber2.getSubscriptionId()).thenReturn("10.35.0.96|*|VM_status|*");
        SubscriptionHolder holder2 = new SubscriptionHolder(subscriber2, new AtomicInteger());

        SubscriptionMatcher matcher = new SubscriptionMatcher();
        matcher.add(holder);
        matcher.add(holder2);

        JsonRpcEvent event = mock(JsonRpcEvent.class);
        when(event.getMethod()).thenReturn("10.35.0.96|virt|VM_migration_status|d4b04c1d-c2bc-41e3-add7");

        Set<SubscriptionHolder> holders = matcher.match(event);
        assertEquals(1, holders.size());
    }
}
