package org.ovirt.vdsm.jsonrpc.client.internal;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import org.codehaus.jackson.JsonNode;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcRequest;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcResponse;

/**
 * Groups responses for batch call and let user to get them when
 * all of them are ready.
 *
 */
public class BatchCall implements Future<List<JsonRpcResponse>>, JsonRpcCall {

    private final CountDownLatch latch;
    private final List<JsonRpcResponse> responses;
    private final List<JsonNode> ids;

    public BatchCall(List<JsonRpcRequest> requests) {
        this.ids = new ArrayList<>();
        this.responses = new ArrayList<>(requests.size());
        this.latch = new CountDownLatch(requests.size());
        updateIds(requests);
    }

    private void updateIds(List<JsonRpcRequest> requests) {
        for (JsonRpcRequest request: requests) {
            this.ids.add(request.getId());
        }
    }

    @Override
    public void addResponse(JsonRpcResponse response) {
        responses.add(response);
        latch.countDown();
    }

    @Override
    public boolean cancel(boolean cancel) {
        return false;
    }

    @Override
    public List<JsonRpcResponse> get() throws InterruptedException,
            ExecutionException {
        latch.await();
        return responses;
    }

    @Override
    public List<JsonRpcResponse> get(long time, TimeUnit unit)
            throws InterruptedException, ExecutionException,
            TimeoutException {
        if (!latch.await(time, unit)) {
            throw new TimeoutException();
        }
        return responses;
    }

    @Override
    public boolean isCancelled() {
        return false;
    }

    @Override
    public boolean isDone() {
        return (latch.getCount() == 0);
    }

    public List<JsonNode> getId() {
        return this.ids;
    }
}
