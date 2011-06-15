# for a "singleton" config object
import ConfigParser

config = ConfigParser.ConfigParser()
config.add_section('vars')
config.set('vars', 'reg_req_interval', '5')
config.set('vars', 'vdsm_conf_file', '/etc/vdsm/vdsm.conf')
config.set('vars', 'logger_conf', '/etc/vdsm-reg/logger.conf')
config.set('vars', 'pidfile', '/var/run/vdsm-reg.pid')
config.set('vars', 'test_socket_timeout', '10')
config.set('vars', 'vdc_host_name', 'None')
config.set('vars', 'vdc_host_ip', 'None')
config.set('vars', 'vdc_host_port', '80')
config.set('vars', 'vdc_authkeys_path', '/rhevm.ssh.key.txt')
config.set('vars', 'vdc_reg_uri', '/SolidICE/VdsAutoRegistration.aspx')
config.set('vars', 'vdc_reg_port', '54321')
config.set('vars', 'upgrade_iso_file', '/data/updates/ovirt-node-image.iso')
config.set('vars', 'vdsm_dir', '/usr/share/vdsm')
