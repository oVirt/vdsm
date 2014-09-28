package org.ovirt.vdsm.jsonrpc.client.internal;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.buildFailedResponse;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.getTimeout;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.jsonToByteArray;

import java.util.Queue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.codehaus.jackson.JsonNode;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcRequest;
import org.ovirt.vdsm.jsonrpc.client.RequestAlreadySentException;
import org.ovirt.vdsm.jsonrpc.client.utils.ResponseTracking;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryContext;

/**
 * Response tracker thread is responsible for tracking and retrying requests.
 * For each connection there is single instance of the thread.
 *
 */
public class ResponseTracker implements Runnable {
    private static Log log = LogFactory.getLog(ResponseTracker.class);
    private static final int TRACKING_TIMEOUT = 500;
    private AtomicBoolean isTracking;
    private final ConcurrentMap<JsonNode, JsonRpcCall> runningCalls = new ConcurrentHashMap<>();
    private ConcurrentMap<JsonNode, ResponseTracking> map = new ConcurrentHashMap<>();
    private Queue<JsonNode> queue = new ConcurrentLinkedQueue<>();

    public ResponseTracker() {
        this.isTracking = new AtomicBoolean(true);
    }

    private void removeRequestFromTracking(JsonNode id) {
        this.queue.remove(id);
        this.map.remove(id);
    }

    public void registerCall(JsonRpcRequest req, JsonRpcCall call) {
        if (this.runningCalls.putIfAbsent(req.getId(), call) != null) {
            throw new RequestAlreadySentException();
        }
    }

    public JsonRpcCall removeCall(JsonNode id) {
        return this.runningCalls.remove(id);
    }

    public void registerTrackingRequest(JsonRpcRequest req, ResponseTracking tracking) {
        this.map.put(req.getId(), tracking);
        this.queue.add(req.getId());
    }

    @Override
    public void run() {
        try {
            while (this.isTracking.get()) {
                TimeUnit.MILLISECONDS.sleep(TRACKING_TIMEOUT);
                for (JsonNode id : queue) {
                    if (!this.runningCalls.containsKey(id)) {
                        removeRequestFromTracking(id);
                        continue;
                    }
                    ResponseTracking tracking = this.map.get(id);
                    if (System.currentTimeMillis() >= tracking.getTimeout()) {
                        RetryContext context = tracking.getContext();
                        context.decreaseAttempts();
                        if (context.getNumberOfAttempts() <= 0) {
                            handleFailure(tracking, id);
                            continue;
                        }
                        try {
                            tracking.getClient().sendMessage(jsonToByteArray(tracking.getRequest().toJson()));
                        } catch (ClientConnectionException e) {
                            handleFailure(tracking, id);
                        }
                        tracking.setTimeout(getTimeout(context.getTimeout(), context.getTimeUnit()));
                    }
                }
            }
        } catch (InterruptedException e) {
            log.warn("Tracker thread intrreupted");
        }
    }

    public void close() {
        this.isTracking.set(false);
    }

    private void handleFailure(ResponseTracking tracking, JsonNode id) {
        this.runningCalls.remove(id);
        removeRequestFromTracking(id);
        tracking.getCall().addResponse(buildFailedResponse(tracking.getRequest()));
        tracking.getClient().disconnect();
    }
}
