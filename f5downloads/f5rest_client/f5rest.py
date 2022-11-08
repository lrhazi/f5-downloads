from datetime import datetime
import requests, os, re
from f5downloads.logger.logger import logger

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class F5rest:
    def __init__(self, username, password, device, verify_ssl=True):
        self.device = device
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._token = None

    @property
    def token(self):
        if not self._token:
            body = {
                'username': self.username,
                'password': self.password,
                'loginProviderName': 'tmos'
            }
            r = requests.post(f'https://{self.device}/mgmt/shared/authn/login',verify=self.verify_ssl,auth=(self.username, self.password), json=body)
            token_response = r.json()

            self._token = token_response['token']['token']
        return self._token

    def upload_file(self, file_path):

        headers = {
            'Content-Type': 'application/octet-stream',
            'X-F5-Auth-Token': self.token
        }

        chunk_size = 512 * 1024
        file_obj = open(file_path, 'rb')
        file_name = os.path.basename(file_path)
        size = os.path.getsize(file_path)
        end_point = 'https://' + self.device + '/mgmt/shared/file-transfer/uploads/' + file_name

        start = 0

        while True:
            file_slice = file_obj.read(chunk_size)
            if not file_slice:
                break

            current_bytes = len(file_slice)
            if current_bytes < chunk_size:
                end = size
            else:
                end = start + current_bytes

            content_range = f'{start}-{end - 1}/{size}'
            headers['Content-Range'] = content_range
            requests.post(end_point,
                          data=file_slice,
                          headers=headers,
                          verify=self.verify_ssl)
            start += current_bytes

    def run_bash_command(self, command):

        headers = {
            'X-F5-Auth-Token': self.token
        }

        payload = {
            'command': 'run',
            'utilCmdArgs': f"-c '{command}'"
        }

        response = requests.post('https://' + self.device + '/mgmt/tm/util/bash', json=payload, verify=self.verify_ssl,
                                 headers=headers).json()
        if 'commandResult' in response:
            logger.debug(f"{self.device}: Command: {command}")
            logger.debug(f"{self.device}: Result: {response['commandResult']}")
            return re.sub('\n$', '', response['commandResult'])
        else:
            return None

    def test_remote_file(self, file_path):
        return self.run_bash_command(f'[ -f "{file_path}" ] && echo 1 || echo 0') == '1';

    def get_geoip_db_version(self):
        geoip_db_version = self.run_bash_command('geoip_lookup | egrep -o "[0-9]+$"')
        if not re.match(r'^[0-9]{8}', geoip_db_version):
            raise Exception(
                'Invalid remote geoip database, run \'geoip_lookup | egrep -o "[0-9]+$"\' and validate the output')
        return geoip_db_version

    def get_geoip_db_version_from_file(self, file_name):
        match = re.match(r'.+(?P<version>[0-9]{8}).+', file_name)
        if match:
            local_version = match.groupdict().get('version')
        else:
            raise (f'Unable to parse remote geoip db version from {file_name}')
        return local_version

    def calculate_dates_difference(self, date1, date2):
        date1 = datetime.strptime(date1, '%Y%m%d')
        date2 = datetime.strptime(date2, '%Y%m%d')
        return abs((date2 - date1).days)

    def update_geoip_db(self, file_path):
        file_name = os.path.basename(file_path)

        local_version = self.get_geoip_db_version_from_file(file_name)
        remote_version = self.get_geoip_db_version()
        version_diff = self.calculate_dates_difference(local_version, remote_version)
        #TODO: Hack. ideally versions should be identical. 
        #   But for some reason the version in the filename is different than actual version inside library.
        if version_diff== 0 or version_diff == 1:
            logger.info(f"{self.device}: Newest version already exists on the device")
            return False

        if not self.test_remote_file('/var/tmp/update_geoipdb.sh'):
            logger.info(f"{self.device}: Updating the geoip update shell script")
            self.upload_file('./update_geoipdb.sh')
            self.run_bash_command('mv /var/config/rest/downloads/update_geoipdb.sh /var/tmp/')

        logger.info(f"{self.device}: Uploading {file_name}")
        self.upload_file(file_path)
        self.upload_file(f'{file_path}.md5')

        self.run_bash_command(f'bash /var/tmp/update_geoipdb.sh {file_name}')

        remote_version = self.get_geoip_db_version()
        version_diff = self.calculate_dates_difference(local_version, remote_version)
        # TODO: Hack. ideally versions should be identical. 
        #   But for some reason the version in the filename is different than actual version inside library.
        if version_diff== 0:
            return True
        elif version_diff == 1:
            logger.warning(f'{self.device}: Update might have failed. local_version={local_version} remote_version={remote_version}')
            return True
        else:
            logger.warning(f'{self.device}: Update failed. local_version={local_version} remote_version={remote_version}')
            return False

