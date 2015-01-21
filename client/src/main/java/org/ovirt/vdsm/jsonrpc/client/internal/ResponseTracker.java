package org.ovirt.vdsm.jsonrpc.client.internal;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.buildErrorResponse;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.buildFailedResponse;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.getTimeout;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.jsonToByteArray;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.mapValues;

import java.util.List;
import java.util.Map;
import java.util.Queue;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.Lock;
import java.util.concurrent.locks.ReentrantLock;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.codehaus.jackson.JsonNode;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcRequest;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcResponse;
import org.ovirt.vdsm.jsonrpc.client.RequestAlreadySentException;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.utils.LockWrapper;
import org.ovirt.vdsm.jsonrpc.client.utils.ResponseTracking;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryContext;

/**
 * Response tracker thread is responsible for tracking and retrying requests. For each connection there is single
 * instance of the thread.
 *
 */
public class ResponseTracker implements Runnable {
    private static Log log = LogFactory.getLog(ResponseTracker.class);
    private static final int TRACKING_TIMEOUT = 500;
    private AtomicBoolean isTracking;
    private final ConcurrentMap<JsonNode, JsonRpcCall> runningCalls = new ConcurrentHashMap<>();
    private ConcurrentMap<JsonNode, ResponseTracking> map = new ConcurrentHashMap<>();
    private ConcurrentMap<String, List<JsonNode>> hostToId = new ConcurrentHashMap<>();
    private Queue<JsonNode> queue = new ConcurrentLinkedQueue<>();
    private final Lock lock = new ReentrantLock();

    public ResponseTracker() {
        this.isTracking = new AtomicBoolean(true);
    }

    private void removeRequestFromTracking(JsonNode id) {
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            this.queue.remove(id);
            ResponseTracking tracking = this.map.remove(id);
            if (tracking != null && tracking.getClient() != null) {
                List<JsonNode> nodes = this.hostToId.get(tracking.getClient().getClientId());
                if (nodes != null) {
                    nodes.remove(id);
                }
            }
        }
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
        JsonNode id = req.getId();
        List<JsonNode> nodes = new CopyOnWriteArrayList<>();
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            this.map.put(id, tracking);
            this.queue.add(id);
            nodes.add(id);
            nodes = this.hostToId.putIfAbsent(tracking.getClient().getClientId(), nodes);
            if (nodes != null) {
                nodes.add(id);
            }
        }
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
        remove(tracking, id, buildFailedResponse(tracking.getRequest()));
        tracking.getClient().disconnect("Vds timeout occured");
    }

    private void remove(ResponseTracking tracking, JsonNode id, JsonRpcResponse response) {
        this.runningCalls.remove(id);
        removeRequestFromTracking(id);
        tracking.getCall().addResponse(response);
    }

    public void processIssue(JsonRpcResponse response) {
        JsonNode error = response.getError();
        Map<String, Object> map = mapValues(error);
        String code = (String) map.get("code");
        String message = (String) map.get("message");
        JsonRpcResponse errorResponse = buildErrorResponse(null, 5022, message);

        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            if (ReactorClient.CLIENT_CLOSED.equals(message)) {
                removeNodes(this.hostToId.get(code), errorResponse);
            } else {
                String hostname = code.substring(0, code.indexOf(":"));
                Set<String> keys = this.hostToId.keySet();
                for (String key : keys) {
                    if (key.startsWith(hostname)) {
                        removeNodes(this.hostToId.get(key), errorResponse);
                    }
                }
            }
        }
    }

    private void removeNodes(List<JsonNode> nodes, JsonRpcResponse errorResponse) {
        for (JsonNode id : nodes) {
            remove(this.map.get(id), id, errorResponse);
        }
    }
}
