# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
import os
import ssl
from vdsm.sslutils import SSLContext

CERT_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..')
CRT_FILE = os.path.join(CERT_DIR, "server.crt")
KEY_FILE = os.path.join(CERT_DIR, "server.key")
OTHER_CRT_FILE = os.path.join(CERT_DIR, "other.crt")
OTHER_KEY_FILE = os.path.join(CERT_DIR, "other.key")

DEAFAULT_SSL_CONTEXT = SSLContext(cert_file=CRT_FILE, key_file=KEY_FILE,
                                  ca_certs=CRT_FILE)


def get_server_socket(key_file, cert_file, socket):
    return ssl.wrap_socket(socket,
                           keyfile=key_file,
                           certfile=cert_file,
                           server_side=False,
                           cert_reqs=ssl.CERT_REQUIRED,
                           ssl_version=ssl.PROTOCOL_TLSv1,
                           ca_certs=cert_file)
