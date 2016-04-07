package org.reactivestreams;

import java.io.IOException;
import java.util.Map;

public interface Publisher<T, S extends Subscriber<T>> {

    /**
     * Request {@link Publisher} to start streaming data.
     * <p>
     * This is a "factory method" and can be called multiple times, each time starting a new {@link Subscription}.
     * <p>
     * Each {@link Subscription} will work for only a single {@link Subscriber}.
     * <p>
     * A {@link Subscriber} should only subscribe once to a single {@link Publisher}.
     * <p>
     * If the {@link Publisher} rejects the subscription attempt or otherwise fails it will
     * signal the error via {@link Subscriber#onError}.
     *
     * @param s the {@link Subscriber} that will consume signals from this {@link Publisher}
     */
    public void subscribe(S s);

    /**
     * Request {@link Publisher} to send data.
     *
     * @param subscriptionId the identifier for {@link Subscriber} who will consume the event
     * @param params the data that needs to be sent to the {@link Subscriber}
     * @throws IOException an exception is thrown if the params cannot be serialized
     */
    public void publish(String subscriptionId, Map<String, Object> params) throws IOException;
}
