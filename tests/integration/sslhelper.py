# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import os
import ssl
import tempfile

from contextlib import contextmanager

import pytest

from vdsm.common import commands
from vdsm.sslutils import SSLContext


CERT_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..')
CRT_FILE = os.path.join(CERT_DIR, "server.crt")
KEY_FILE = os.path.join(CERT_DIR, "server.key")

DEAFAULT_SSL_CONTEXT = SSLContext(cert_file=CRT_FILE, key_file=KEY_FILE,
                                  ca_certs=CRT_FILE)

_PASSWD = "pass:secretpassphrase"


@contextmanager
def _generate_key_file():
    with tempfile.NamedTemporaryFile(suffix=".pass.key") as pass_key_file, \
            tempfile.NamedTemporaryFile(suffix=".key") as key_file:
        commands.run([
            "openssl", "genrsa", "-des3", "-passout", _PASSWD, "-out",
            pass_key_file.name, "2048"
        ])
        commands.run([
            "openssl", "rsa", "-passin", _PASSWD, "-in", pass_key_file.name,
            "-out", key_file.name
        ])
        yield key_file


@contextmanager
def _generate_csr_file(key_file):
    with tempfile.NamedTemporaryFile(suffix=".csr") as csr_file:
        commands.run([
            "openssl", "req", "-new", "-key", key_file.name, "-out",
            csr_file.name, "-subj", "/C=US/ST=Bar/L=Foo/O=Dis/CN=::1"
        ])
        yield csr_file


@contextmanager
def _generate_cert_file(csr_file, key_file):
    with tempfile.NamedTemporaryFile(suffix=".crt") as cert_file:
        commands.run([
            "openssl", "x509", "-req", "-days", "365", "-in", csr_file.name,
            "-signkey", key_file.name, "-out", cert_file.name
        ])
        yield cert_file


@contextmanager
def generate_key_cert_pair():
    with _generate_key_file() as key_file:
        with _generate_csr_file(key_file) as csr_file:
            with _generate_cert_file(csr_file, key_file) as cert_file:
                yield key_file.name, cert_file.name


@pytest.fixture(scope="session")
def key_cert_pair():
    with generate_key_cert_pair() as key_cert:
        yield key_cert


def get_server_socket(key_file, cert_file, socket):
    return ssl.wrap_socket(socket,
                           keyfile=key_file,
                           certfile=cert_file,
                           server_side=False,
                           cert_reqs=ssl.CERT_REQUIRED,
                           ca_certs=cert_file)
