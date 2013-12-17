package org.ovirt.vdsm.jsonrpc.client;

/**
 * Exception used to inform user that there is an attempt to sent
 * the same request second time.
 *
 */
public class RequestAlreadySentException extends RuntimeException {

    private static final long serialVersionUID = 3741061950026883647L;

    public RequestAlreadySentException() {
    }

    public RequestAlreadySentException(String message) {
        super(message);
    }

    public RequestAlreadySentException(Throwable cause) {
        super(cause);
    }

    public RequestAlreadySentException(String message, Throwable cause) {
        super(message, cause);
    }

    public RequestAlreadySentException(String message, Throwable cause,
            boolean enableSuppression, boolean writableStackTrace) {
        super(message, cause, enableSuppression, writableStackTrace);
    }

}
