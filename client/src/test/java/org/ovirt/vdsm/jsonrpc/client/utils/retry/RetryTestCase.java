package org.ovirt.vdsm.jsonrpc.client.utils.retry;

import static junit.framework.Assert.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.stub;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.io.IOException;
import java.net.ConnectException;
import java.util.concurrent.Callable;

import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryPolicy;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.Retryable;

public class RetryTestCase {

    @SuppressWarnings("unchecked")
    @Test
    public void testRetry() throws Exception {
        // Given
        Callable<Object> callable = mock(Callable.class);
        RetryPolicy policy = new RetryPolicy(5, 3);
        Retryable<Object> retryable = new Retryable<>(callable, policy);

        // When
        retryable.call();

        // Then
        verify(callable, times(1)).call();
    }

    @SuppressWarnings("unchecked")
    @Test
    public void testRetryWithException() throws Exception {
        // Given
        Callable<Object> callable = mock(Callable.class);
        RetryPolicy policy = new RetryPolicy(5, 3, IOException.class);
        Retryable<Object> retryable = new Retryable<>(callable, policy);
        stub(callable.call())
                .toThrow(new IOException())
                .toThrow(new IOException())
                .toReturn(new Object());

        // When
        retryable.call();

        // Then
        verify(callable, times(3)).call();
    }

    @SuppressWarnings("unchecked")
    @Test(expected = IOException.class)
    public void testRetryWithNoSuccess() throws Exception {
        // Given
        Callable<Object> callable = mock(Callable.class);
        RetryPolicy policy = new RetryPolicy(5, 3, IOException.class);
        Retryable<Object> retryable = new Retryable<>(callable, policy);
        stub(callable.call())
                .toThrow(new ConnectException())
                .toThrow(new IOException())
                .toThrow(new IOException());

        // When
        retryable.call();
    }

    @SuppressWarnings("unchecked")
    @Test(expected = IOException.class)
    public void testRetryWithDifferentException() throws Exception {
        // Given
        Callable<Object> callable = mock(Callable.class);
        RetryPolicy policy = new RetryPolicy(5, 3, IllegalArgumentException.class);
        Retryable<Object> retryable = new Retryable<>(callable, policy);
        stub(callable.call()).toThrow(new IOException());

        // When
        retryable.call();
    }

    @SuppressWarnings("unchecked")
    @Test
    public void testRetryWithValue() throws Exception {
        // Given
        String value = "Hello World!";
        Callable<String> callable = mock(Callable.class);
        RetryPolicy policy = new RetryPolicy(5, 3, IOException.class);
        Retryable<String> retryable = new Retryable<>(callable, policy);
        when(callable.call()).thenReturn(value);

        // When
        String calledValue = retryable.call();

        // Then
        verify(callable, times(1)).call();
        assertEquals(value, calledValue);
    }

    @SuppressWarnings("unchecked")
    @Test(expected = IOException.class)
    public void testRetryWithInfiniteNumberOfRetries() throws Exception {
        // Given
        Callable<Object> callable = mock(Callable.class);
        RetryPolicy policy = new RetryPolicy(5, 0, IOException.class);
        Retryable<Object> retryable = new Retryable<>(callable, policy);
        stub(callable.call())
                .toThrow(new IOException());

        // When
        retryable.call();
    }
}
