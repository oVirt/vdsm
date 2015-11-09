package org.ovirt.vdsm.jsonrpc.client.events;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.ALL;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.SUBSCRIPTION_ALL;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.isEmpty;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.parse;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.CopyOnWriteArrayList;

import org.ovirt.vdsm.jsonrpc.client.JsonRpcEvent;

/**
 * Matcher is responsible for holding all subscriptions and match them to incoming events.
 *
 * Matching process is based on subscription id which is represented by 4 subscription types:
 *
 *  &lt;receiver&gt;.&lt;component&gt;.&lt;operation_id&gt;.&lt;unique_id&gt;
 *
 *  &lt;receiver&gt;     - Uniquely identifies host from which event arrived
 *  &lt;component&gt;    - Logical component like: Storage, Virt etc
 *  &lt;operation_id&gt; - Operation identifier like Image.create
 *  &lt;unique_id&gt;    - Identifier of a specific operation
 *
 *  User can subscribe to all events by defining '*' for each subscription type.
 *
 *  Registration for all possible events like '*|*|*|*' is not allowed.
 *
 *  User can register for specific component operation using '*|storage|*|*' which
 *  means that all events triggered by storage component are delivered to a subscriber.
 */
public class SubscriptionMatcher {

    private ConcurrentMap<String, List<SubscriptionHolder>> receiver = new ConcurrentHashMap<>();
    private ConcurrentMap<String, List<SubscriptionHolder>> component = new ConcurrentHashMap<>();
    private ConcurrentMap<String, List<SubscriptionHolder>> operation = new ConcurrentHashMap<>();
    private ConcurrentMap<String, SubscriptionHolder> unique_id = new ConcurrentHashMap<>();

    private interface Predicate {
        boolean apply(int one, int two);
    }

    /**
     * Adds a {@link SubscriptionHolder} which will be used for event matching
     *
     * @param holder Instance holding subscription information.
     */
    public void add(SubscriptionHolder holder) {
        if (SUBSCRIPTION_ALL.equals(holder.getId())) {
            throw new IllegalArgumentException("Can't subscribe to all events");
        }
        String[] ids = parse(holder.getId());
        try {
            String uid = ids[3];
            if (!ALL.equals(uid)) {
                validateKey(uid);
                this.unique_id.put(uid, holder);
            } else {
                String opKey = ids[2];
                if (!ALL.equals(opKey)) {
                    update(this.operation, opKey, holder);
                }
                String compKey = ids[1];
                if (!ALL.equals(compKey)) {
                    update(this.component, compKey, holder);
                }
                String rKey = ids[0];
                if (!ALL.equals(rKey)) {
                    update(this.receiver, rKey, holder);
                }
            }
        } catch (IllegalArgumentException e) {
            remove(holder);
            throw e;
        }
    }

    private void update(ConcurrentMap<String, List<SubscriptionHolder>> map, String key, SubscriptionHolder holder) {
        validateKey(key);
        List<SubscriptionHolder> holders = new CopyOnWriteArrayList<>();
        holders.add(holder);
        holders = map.putIfAbsent(key, holders);
        if (holders != null) {
            holders.add(holder);
        }
    }

    private void validateKey(String key) {
        if (isEmpty(key)) {
            throw new IllegalArgumentException("Wrong id format");
        }
    }

    /**
     * Matches current subscriptions to an event and returns a <code>Set</code>
     * containing all subscriptions that match for this event processing.
     *
     * @param event Incoming event used to match subscribers.
     * @return A {@link Set} with matched subscriptions.
     */
    public Set<SubscriptionHolder> match(JsonRpcEvent event) {
        String[] ids = parse(event.getMethod());
        Set<SubscriptionHolder> subscriptions = new HashSet<>();
        SubscriptionHolder holder = this.unique_id.get(ids[3]);
        if (holder != null) {
            subscriptions.add(holder);
        }
        Predicate predicate = new Predicate() {

            @Override
            public boolean apply(int one, int two) {
                return one == two;
            }
        };
        addHolders(subscriptions, this.operation, 2, ids, predicate);
        addHolders(subscriptions, this.component, 1, ids, predicate);
        addHolders(subscriptions, this.receiver, 0, ids, new Predicate() {

            @Override
            public boolean apply(int one, int two) {
                return two > 0;
            }
        });
        return subscriptions;
    }

    private void addHolders(Set<SubscriptionHolder> holders,
            ConcurrentMap<String, List<SubscriptionHolder>> map,
            int key, String[] ids, Predicate predicate) {
        List<SubscriptionHolder> values = map.get(ids[key]);
        if (values == null) {
            return;
        }
        for (SubscriptionHolder value : values) {
            List<String> fids = value.getFilteredId();
            int size = fids.size();
            fids.retainAll(Arrays.asList(ids));
            if (predicate.apply(size, fids.size())) {
                holders.add(value);
            }
        }
    }

    /**
     * Used during removal of a subscription.
     *
     * @param holder Object holding information about subscription being removed.
     */
    public void remove(SubscriptionHolder holder) {
        String[] ids = holder.getParsedId();
        String uid = ids[3];
        this.unique_id.remove(uid);
        clean(this.operation, ids[2], holder);
        clean(this.component, ids[1], holder);
        clean(this.receiver, ids[0], holder);
    }

    private void clean(ConcurrentMap<String, List<SubscriptionHolder>> map, String key, SubscriptionHolder holder) {
        List<SubscriptionHolder> holders = map.get(key);
        if (holders != null) {
            if (holders.size() > 1) {
                holders.remove(holder);
            } else {
                map.remove(key);
            }
        }
    }
}
