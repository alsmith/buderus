import base64
import binascii
import json
import md5
import requests
import struct
from Crypto.Cipher import AES

# Acknowledgements:
# KM200 control code inspired by https://github.com/rthill/buderus

class Buderus():
    INTERRUPT = '\1'
    PAD = '\0'

    def __init__(self, host, userPassword, gatewayPassword):
        self.__ua = "TeleHeater"
        self.__content_type = "application/json"
        self._host = host

        # This will need to be an array with 32 bytes total...
        salt = [ 0x00, 0x01, 0x02, ... ]

        bsalt = struct.pack('B' * len(salt), *salt)

        m = md5.new()
        m.update(gatewayPassword)
        m.update(bsalt)
        k1 = m.hexdigest()

        m = md5.new()
        m.update(bsalt)
        m.update(userPassword)
        k2 = m.hexdigest()

        self._key = binascii.unhexlify(k1+k2)

    def _json_encode(self, value):
        return json.dumps({'value': value})

    def _encrypt(self, plain):
        plain = plain + ((AES.block_size - len(plain)) % AES.block_size) * self.PAD
        encobj = AES.new(self._key, AES.MODE_ECB)
        data = encobj.encrypt(plain)
        return base64.b64encode(data)

    def _decrypt(self, enc):
        decobj = AES.new(self._key, AES.MODE_ECB)
        data = decobj.decrypt(base64.b64decode(enc))
        data = data.rstrip(self.PAD.encode()).rstrip(self.INTERRUPT.encode())
        return data

    def get_data(self, path):
        try:
            url = 'http://' + self._host + path
            resp = requests.get(url, headers = {'User-agent': self.__ua, 'Accept': self.__content_type})
            plain = self._decrypt(resp.text)
            return plain
        except Exception as e:
            return None

    def set_data(self, path, data):
        payload = self._encrypt(self._json_encode(data))
        print self._decrypt(payload)
        url = 'http://' + self._host + path
        headers = {"User-agent": self.__ua, "Content-Type": self.__content_type}

        request = requests.put(url, data=payload, headers=headers)
        if not request.status_code == 204:
            raise Exception('HTTP status: %s' % request.status_code)

