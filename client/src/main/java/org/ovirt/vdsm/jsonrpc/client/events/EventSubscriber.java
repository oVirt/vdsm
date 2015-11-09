package org.ovirt.vdsm.jsonrpc.client.events;

import java.util.Map;

import org.reactivestreams.Subscriber;
import org.reactivestreams.Subscription;


/**
 * Subscription id contains &lt;receiver&gt;.&lt;component&gt;.&lt;operation_id&gt;.&lt;unique_id&gt;.
 *
 */
public abstract class EventSubscriber implements Subscriber<Map<String, Object>> {

    private String subscriptionId;

    /**
     * @param subscriptionId subscription id which is used to match an event to subscription.
     */
    public EventSubscriber(String subscriptionId) {
        this.subscriptionId = subscriptionId;
    }

    /*
     * (non-Javadoc)
     * @see org.reactivestreams.Subscriber#onSubscribe(org.reactivestreams.Subscription)
     */
    public abstract void onSubscribe(Subscription s);

    /*
     * (non-Javadoc)
     * @see org.reactivestreams.Subscriber#onNext(java.lang.Object)
     */
    public abstract void onNext(Map<String, Object> t);

    /*
     * (non-Javadoc)
     * @see org.reactivestreams.Subscriber#onError(java.lang.Throwable)
     */
    public abstract void onError(Throwable t);


    /*
     * (non-Javadoc)
     * @see org.reactivestreams.Subscriber#onComplete()
     */
    public abstract void onComplete();

    /**
     * @return subscription id which is used to match incoming events.
     */
    public String getSubscriptionId() {
        return this.subscriptionId;
    }
}
