package org.ovirt.vdsm.jsonrpc.client;

import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.codehaus.jackson.map.ObjectMapper;
import org.codehaus.jackson.node.ObjectNode;
import org.codehaus.jackson.node.TextNode;

/**
 * Utility class which helps to build {@link JsonRpcRequest} by providing
 * information like method name and parameters.
 *
 */
public class RequestBuilder {

    private final static ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private final ObjectNode parameters;
    private final String methodName;

    /**
     * Creates builder with required methodName.
     */
    public RequestBuilder(String methodName) {
        this.parameters = OBJECT_MAPPER.createObjectNode();
        this.methodName = methodName;
    }

    /**
     * Adds parameter which is required by method runtime.
     * @param name - Name of the parameter.
     * @param value - Value of the parameter.
     * @return {@link RequestBuilder} to let add more parameters.
     */
    public RequestBuilder withParameter(String name, String value) {
        this.parameters.put(name, value);
        return this;
    }

    /**
     * Adds parameter which is optional by method runtime and if not
     * provided default value will be used during method execution.
     * @param name - Name of the parameter.
     * @param value - Value of the parameter or <code>null</code>.
     * @return {@link RequestBuilder} to let add more parameters.
     */
    public RequestBuilder withOptionalParameter(String name, String value) {
        if (value != null && !"".equals(value.trim())) {
            return withParameter(name, value);
        }
        return this;
    }

    /**
     * Adds <code>List</code> of values which is optional by method
     * runtime and if not provided default value will be used during
     * method execution.
     * @param name - Name of the parameter.
     * @param value - <code>List</code> of values which may be <code>null</code>
     *                or empty <code>List</code>.
     * @return {@link RequestBuilder} to let add more parameters.
     */
    @SuppressWarnings("rawtypes")
    public RequestBuilder withOptionalParameterAsList(String name, List value) {
        if (value != null && !value.isEmpty()) {
            return withParameter(name, value);
        }
        return this;
    }

    /**
     * Adds <code>Map</code> of values which is optional by method
     * runtime and if not provided default value will be used during
     * method execution.
     * @param name - Name of the parameter.
     * @param value - <code>Map</code> of values which may be <code>null</code>
     *                or empty <code>Map</code>.
     * @return {@link RequestBuilder} to let add more parameters.
     */
    @SuppressWarnings("rawtypes")
    public RequestBuilder withOptionalParameterAsMap(String name, Map value) {
        if (value != null && !value.isEmpty()) {
            return withParameter(name, value);
        }
        return this;
    }

    /**
     * Adds parameter which is required by method runtime.
     * @param name - Name of the parameter.
     * @param value - Value of the parameter which is different than {@link String}.
     * @return {@link RequestBuilder} to let add more parameters.
     */
    public RequestBuilder withParameter(String name, Object value) {
        this.parameters.putPOJO(name, value);
        return this;
    }

    /**
     * Builds {@link JsonRpcRequest} based on provided method name, parameter and
     * generates id using {@link UUID}.
     * @return Request object.
     */
    public JsonRpcRequest build() {
        final TextNode id = this.parameters.textNode(UUID.randomUUID().toString());
        return new JsonRpcRequest(this.methodName, this.parameters, id);
    }
}
