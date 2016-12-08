package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import java.util.List;

import org.ovirt.vdsm.jsonrpc.client.internal.ClientPolicy;

public class StompClientPolicy extends ClientPolicy {

    private String requestQueue;
    private String responseQueue;
    private String eventQueue;

    public StompClientPolicy(int retryTimeOut,
            int retryNumber,
            int heartbeat,
            Class<? extends Exception> retryableException,
            String requestQueue,
            String responseQueue) {
        super(retryTimeOut, retryNumber, heartbeat, retryableException);
        this.requestQueue = requestQueue;
        this.responseQueue = responseQueue;
    }

    public StompClientPolicy(int retryTimeOut,
            int retryNumber,
            int incomingHeartbeat,
            int outgoingHeartbeat,
            List<Class<? extends Exception>> retryableExceptions,
            String requestQueue,
            String responseQueue) {
        super(retryTimeOut, retryNumber, incomingHeartbeat, outgoingHeartbeat, retryableExceptions);
        this.requestQueue = requestQueue;
        this.responseQueue = responseQueue;
    }

    public StompClientPolicy(int retryTimeOut, int retryNumber, int heartbeat, String requestQueue,
            String responseQueue) {
        super(retryTimeOut, retryNumber, heartbeat);
        this.requestQueue = requestQueue;
        this.responseQueue = responseQueue;
    }

    public String getRequestQueue() {
        return requestQueue;
    }

    public String getResponseQueue() {
        return responseQueue;
    }

    public String getEventQueue() {
        return this.eventQueue;
    }

    public void setEventQueue(String eventQueue) {
        this.eventQueue = eventQueue;
    }

    @Override
    public ClientPolicy clone() throws CloneNotSupportedException {
        StompClientPolicy policy = new StompClientPolicy(this.getRetryTimeOut(),
                this.getRetryNumber(),
                this.getIncomingHeartbeat(),
                this.getOutgoingHeartbeat(),
                this.getExceptions(),
                this.requestQueue,
                this.responseQueue);
        policy.setEventQueue(this.eventQueue);
        return policy;
    }
}
