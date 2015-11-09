package org.ovirt.vdsm.jsonrpc.client.events;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.ALL;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.parse;

import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.locks.Lock;
import java.util.concurrent.locks.ReentrantLock;

import org.ovirt.vdsm.jsonrpc.client.JsonRpcEvent;
import org.ovirt.vdsm.jsonrpc.client.utils.LockWrapper;

/**
 * Holds subscription information such as amount of messages requested by {@link EventSubscriber}. When events are not
 * processed immediately they are queued in here. This holder contains instance of subscription itself.
 *
 */
public class SubscriptionHolder {
    private EventSubscriber subscriber;
    private Deque<JsonRpcEvent> events = new ConcurrentLinkedDeque<>();
    private volatile AtomicInteger count;
    private String[] parsedId;
    private List<String> filteredId;
    private Lock lock = new ReentrantLock();

    /**
     * Creates a holder which subscriber instance and count and it prepares subscription id representation for event
     * matching.
     *
     * @param subscriber
     *            Instance of @link {@link EventSubscriber}.
     * @param count
     *            Represent current number of events requested by subscriber.
     */
    public SubscriptionHolder(EventSubscriber subscriber, AtomicInteger count) {
        this.subscriber = subscriber;
        this.count = count;
        this.parsedId = parse(getId());
        filter();
    }

    /**
     * @return subscription id as complete string e.q. &lt;receiver&gt;.&lt;component&gt;.&lt;operation_id&gt;.&lt;unique_id&gt;.
     */
    public String getId() {
        return this.subscriber.getSubscriptionId();
    }

    /**
     * @return parsed subscription id as string array. Each entry represents subscription type.
     */
    public String[] getParsedId() {
        return this.parsedId;
    }

    private void filter() {
        String[] ids = this.getParsedId();
        this.filteredId = new ArrayList<>();
        for (String id : ids) {
            if (!ALL.equals(id)) {
                this.filteredId.add(id);
            }
        }
    }

    /**
     * @return Filtered subscription id which do not contains all filter '*'
     */
    public List<String> getFilteredId() {
        return new ArrayList<String>(this.filteredId);
    }

    /**
     * @return Checks and return information whether subscriber can process events based on count defined.
     */
    public boolean canProcess() {
        return this.count.get() > 0;
    }

    /**
     * @return An event for processing if there is any and if subscriber is willing to process more events.
     */
    public JsonRpcEvent canProcessMore() {
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            if (!this.events.isEmpty() && this.count.getAndDecrement() > 0) {
                return this.events.removeLast();
            }
            return null;
        }
    }

    /**
     * Queues not processed event for later processing.
     *
     * @param event
     *            An event to be queued.
     */
    public void putEvent(JsonRpcEvent event) {
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            this.events.addFirst(event);
        }
    }

    /**
     * @return Subscribed hold by this instance.
     */
    public EventSubscriber getSubscriber() {
        return this.subscriber;
    }

    /**
     * Clean event queue.
     */
    public void clean() {
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            this.events.clear();
        }
    }
}
