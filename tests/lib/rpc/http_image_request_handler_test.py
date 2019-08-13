#
# Copyright 2019 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import json
import logging
import socket
import time

import pytest
import six

from six.moves import http_client

from testlib import mock

from vdsm.common import concurrent
from vdsm.rpc import http
from vdsm.rpc.http import ImageRequestHandler as IRH


TASK_ID = "my-task-id"
POOL_UUID = "my-pool-uuid"
DOMAIN_UUID = "my-domain-uuid"
IMAGE_UUID = "my-image-uuid"
IMAGE_DATA = b"my-image-data"


logger = logging.getLogger("image-request-handler-test")


@pytest.fixture
def server_thread():

    def server_proc(sock, callback):
        conn, addr = sock.accept()
        callback(conn, addr)

    server = None
    sock = None
    thread = None
    try:
        server = http.Server(None, logger)
        server.start()
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(5)
        sock.bind(("127.0.0.1", 0))
        sock.listen(5)
        thread = concurrent.thread(server_proc, (sock, server.add_socket,),
                                   log=logger)
        thread.start()
        yield sock.getsockname()
    finally:
        if thread is not None:
            thread.join()
        if sock is not None:
            sock.close()
        if server is not None:
            server.stop()


@pytest.fixture
def irh_connection(server_thread):

    def send_request(verb, headers, body):
        host, port = server_thread
        conn = http_client.HTTPConnection(host, port)
        conn.request(verb, "http://not.important.com", body, headers)
        return conn

    return send_request


@pytest.mark.parametrize(
    "verb,headers,expected_error", [
        pytest.param(
            "GET",
            {},
            "missing header Range",
            id="GET - missing 'Range' header"
        ),
        pytest.param(
            "GET",
            {
                IRH.HEADER_RANGE: "invalid range"
            },
            "Unsupported range",
            id="GET - invalid 'Range' header"
        ),
        pytest.param(
            "GET",
            {
                IRH.HEADER_RANGE: "bytes=0-2000",
                IRH.HEADER_DOMAIN: "domain",
                IRH.HEADER_IMAGE: "image"
            },
            "missing or empty required header",
            id="GET - missing storage pool header"
        ),
        pytest.param(
            "GET",
            {
                IRH.HEADER_RANGE: "bytes=0-2000",
                IRH.HEADER_POOL: "pool",
                IRH.HEADER_IMAGE: "image"
            },
            "missing or empty required header",
            id="GET - missing storage domain header"
        ),
        pytest.param(
            "GET",
            {
                IRH.HEADER_RANGE: "bytes=0-2000",
                IRH.HEADER_POOL: "pool",
                IRH.HEADER_DOMAIN: "domain"
            },
            "missing or empty required header",
            id="GET - missing image id header"
        ),
        pytest.param(
            "PUT",
            {},
            "missing or empty required header",
            id="PUT - missing content length header"
        ),
        pytest.param(
            "PUT",
            {
                IRH.HEADER_CONTENT_LENGTH: "zorro"
            },
            "not int value",
            id="PUT - invalid content length header"
        ),
        pytest.param(
            "PUT",
            {
                IRH.HEADER_CONTENT_LENGTH: "2000",
                IRH.HEADER_DOMAIN: "domain",
                IRH.HEADER_IMAGE: "image"
            },
            "missing or empty required header",
            id="PUT - missing storage pool header"
        ),
        pytest.param(
            "PUT",
            {
                IRH.HEADER_CONTENT_LENGTH: "2000",
                IRH.HEADER_POOL: "pool",
                IRH.HEADER_IMAGE: "image"
            },
            "missing or empty required header",
            id="PUT - missing storage domain header"
        ),
        pytest.param(
            "PUT",
            {
                IRH.HEADER_CONTENT_LENGTH: "2000",
                IRH.HEADER_POOL: "pool",
                IRH.HEADER_DOMAIN: "domain"
            },
            "missing or empty required header",
            id="PUT - missing image id header"
        ),
    ]
)
@pytest.mark.skipif(six.PY3, reason="incompatible with py3's 'http' module")
def test_irh_should_report_missing_headers(
        caplog, irh_connection, verb, headers, expected_error):
    response = irh_connection(verb, headers, b"").getresponse()

    errors_logged = [logged_msg
                     for (_, logging_level, logged_msg) in caplog.record_tuples
                     if logging_level == logging.ERROR]

    assert response.status == http_client.BAD_REQUEST
    assert any(expected_error in error for error in errors_logged), \
        "expected error '{}' not found in logs".format(expected_error)


@pytest.fixture
def api_image_mock(monkeypatch):
    with monkeypatch.context() as m:
        image_mock = mock.Mock()
        m.setattr(http.API, 'Image', image_mock)
        yield image_mock


@pytest.fixture
def image_mock(api_image_mock):
    image = mock.Mock()
    api_image_mock.return_value = image
    yield image


@pytest.fixture
def image_operation_mock(image_operation_status_code):
    image_operation = mock.Mock()
    image_operation.return_value = {
        "uuid": TASK_ID,
        "status": {
            "code": image_operation_status_code
        }
    }
    return image_operation


@pytest.fixture
def upload_to_stream_mock(image_operation_mock, image_mock):
    image_mock.uploadToStream = image_operation_mock
    return image_operation_mock


@pytest.fixture
def download_from_stream_mock(image_operation_mock, image_mock):
    image_mock.downloadFromStream = image_operation_mock
    return image_operation_mock


@pytest.fixture
def finish_image_upload(upload_to_stream_mock):

    def finish_upload_cb():
        method_args = upload_to_stream_mock.mock_calls[0][1][0]
        operation_end_callback = upload_to_stream_mock.mock_calls[0][1][1]
        wfile = method_args["fileObj"]
        wfile.write(IMAGE_DATA)
        operation_end_callback()

    return finish_upload_cb


@pytest.fixture
def finish_image_download(download_from_stream_mock):

    def finish_download_cb():
        # we need to wait for http server to get to a moment
        # when it already called mocked 'downloadFromStream'
        while not download_from_stream_mock.mock_calls:
            time.sleep(0.01)
        method_args = download_from_stream_mock.mock_calls[0][1][0]
        operation_end_callback = download_from_stream_mock.mock_calls[0][1][1]
        operation_end_callback()
        rfile = method_args["fileObj"]
        data = rfile.read(len(IMAGE_DATA))
        return data

    return finish_download_cb


def get_headers(range_boundary=(len(IMAGE_DATA) - 1)):
    return {
        IRH.HEADER_POOL: POOL_UUID,
        IRH.HEADER_DOMAIN: DOMAIN_UUID,
        IRH.HEADER_IMAGE: IMAGE_UUID,
        IRH.HEADER_RANGE: "bytes=0-{}".format(range_boundary),
    }


def put_headers():
    return {
        IRH.HEADER_POOL: POOL_UUID,
        IRH.HEADER_DOMAIN: DOMAIN_UUID,
        IRH.HEADER_IMAGE: IMAGE_UUID,
    }


@pytest.mark.parametrize(
    "verb,headers,body,image_operation_status_code,expected_status", [
        pytest.param(
            "GET",
            get_headers(),
            b"",
            0,
            http_client.PARTIAL_CONTENT,
            id="valid get"
        ),
    ]
)
@pytest.mark.skipif(six.PY3, reason="incompatible with py3's 'http' module")
def test_irh_should_retrieve_image(
        irh_connection, api_image_mock, finish_image_upload, verb, headers,
        body, expected_status):
    response = irh_connection(verb, headers, body).getresponse()
    finish_image_upload()

    api_image_mock.assert_called_with(IMAGE_UUID, POOL_UUID, DOMAIN_UUID)

    assert response.status == expected_status
    assert response.getheader(IRH.HEADER_CONTENT_LENGTH) == \
        str(len(IMAGE_DATA))
    assert response.getheader(IRH.HEADER_CONTENT_RANGE) == \
        "bytes 0-{}".format(len(IMAGE_DATA) - 1)
    assert response.getheader(IRH.HEADER_TASK_ID) == TASK_ID
    assert response.read() == IMAGE_DATA


@pytest.mark.parametrize(
    "verb,headers,body,image_operation_status_code,expected_status", [
        pytest.param(
            "GET",
            get_headers(range_boundary=8),
            b"",
            0,
            http_client.PARTIAL_CONTENT,
            id="valid partial get"
        )
    ]
)
@pytest.mark.skipif(six.PY3, reason="incompatible with py3's 'http' module")
def test_irh_should_retrieve_partial_image(
        irh_connection, api_image_mock, finish_image_upload, verb, headers,
        body, expected_status):
    response = irh_connection(verb, headers, body).getresponse()
    finish_image_upload()

    api_image_mock.assert_called_with(IMAGE_UUID, POOL_UUID, DOMAIN_UUID)

    assert response.status == expected_status
    assert response.getheader(IRH.HEADER_CONTENT_LENGTH) == "9"
    assert response.getheader(IRH.HEADER_CONTENT_RANGE) == "bytes 0-8"
    assert response.getheader(IRH.HEADER_TASK_ID) == TASK_ID
    assert response.read() == IMAGE_DATA[:9]


@pytest.mark.parametrize(
    "verb,headers,body,image_operation_status_code,expected_status", [
        pytest.param(
            "PUT",
            put_headers(),
            IMAGE_DATA,
            0,
            http_client.OK,
            id="valid put"
        ),
    ]
)
@pytest.mark.skipif(six.PY3, reason="incompatible with py3's 'http' module")
def test_irh_should_save_image(
        irh_connection, api_image_mock, finish_image_download, verb, headers,
        body, expected_status):
    conn = irh_connection(verb, headers, body)
    read_image_data = finish_image_download()
    response = conn.getresponse()

    api_image_mock.assert_called_with(IMAGE_UUID, POOL_UUID, DOMAIN_UUID)

    assert response.status == expected_status
    assert response.getheader(IRH.HEADER_TASK_ID) == TASK_ID
    assert read_image_data == IMAGE_DATA


@pytest.mark.parametrize(
    "verb,headers,body,image_operation_status_code", [
        pytest.param(
            "GET",
            get_headers(),
            b"",
            100,
            id="unsuccessful upload"
        ),
        pytest.param(
            "PUT",
            put_headers(),
            IMAGE_DATA,
            100,
            id="unsuccessful download"
        ),
    ]
)
@pytest.mark.skipif(six.PY3, reason="incompatible with py3's 'http' module")
def test_irh_should_respond_with_error_after_unsuccessful_image_operation(
        irh_connection, api_image_mock, upload_to_stream_mock,
        download_from_stream_mock, verb, headers, body,
        image_operation_status_code):
    response = irh_connection(verb, headers, body).getresponse()

    api_image_mock.assert_called_with(IMAGE_UUID, POOL_UUID, DOMAIN_UUID)

    assert response.status == http_client.INTERNAL_SERVER_ERROR
    assert response.getheader(IRH.HEADER_CONTENT_TYPE) == "application/json"

    response_body = json.loads(response.read())

    assert response_body["status"]["code"] == image_operation_status_code
    assert response_body["uuid"] == TASK_ID


@pytest.mark.parametrize(
    "verb,headers,body", [
        pytest.param(
            "GET",
            get_headers(),
            b"",
            id="GET - unsuccessful 'Image' api call"
        ),
        pytest.param(
            "PUT",
            put_headers(),
            IMAGE_DATA,
            id="PUT - unsuccessful 'Image' api call"
        ),
    ]
)
@pytest.mark.skipif(six.PY3, reason="incompatible with py3's 'http' module")
def test_irh_should_respond_with_error_after_unexpected_exception(
        irh_connection, api_image_mock, verb, headers, body):
    api_image_mock.side_effect = ValueError("smth went wrong")
    response = irh_connection(verb, headers, body).getresponse()

    assert response.status == http_client.INTERNAL_SERVER_ERROR
