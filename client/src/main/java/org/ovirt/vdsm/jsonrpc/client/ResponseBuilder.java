package org.ovirt.vdsm.jsonrpc.client;

import java.util.List;
import java.util.Map;

import org.codehaus.jackson.JsonNode;
import org.codehaus.jackson.map.ObjectMapper;

/**
 * Utility class which helps to build {@link JsonRpcResponse} by providing
 * information like response or error.
 *
 */
public class ResponseBuilder {
    private final static ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private JsonNode result;
    private JsonNode error;
    private JsonNode id;

    /**
     * Creates builder with required response id.
     */
    public ResponseBuilder(JsonNode id) {
        this.id = id;
    }

    /**
     * Adds result <code>Map</code> to the response.
     * @param result <code>Map</code> with response data.
     * @return {@link ResponseBuilder} to let add more parameters.
     */
    public ResponseBuilder withResult(Map<String, Object> result) {
        this.result = OBJECT_MAPPER.valueToTree(result);
        return this;
    }

    /**
     * Adds result <code>String</code> to the response.
     * @param result
     *            <code>String</code> with response data.
     * @return {@link ResponseBuilder} to let add more parameters.
     */
    public ResponseBuilder withResult(String result) {
        this.result = OBJECT_MAPPER.valueToTree(result);
        return this;
    }

    /**
     * Adds result <code>List</code> to the response.
     * @param result
     *            <code>List</code> with response data.
     * @return {@link ResponseBuilder} to let add more parameters.
     */
    public ResponseBuilder withResult(List<Object> result) {
        this.result = OBJECT_MAPPER.valueToTree(result);
        return this;
    }

    /**
     * Adds error <code>Map</code> to the response.
     * @param error
     *            <code>Map</code> with error data.
     * @return {@link ResponseBuilder} to let add more parameters.
     */
    public ResponseBuilder withError(Map<String, Object> error) {
        this.result = OBJECT_MAPPER.valueToTree(error);
        return this;
    }

    /**
     * Builds {@link JsonRpcResponse} based on provided id, result and error.
     * @return Response object.
     */
    public JsonRpcResponse build() {
        return new JsonRpcResponse(result, error, id);
    }
}
