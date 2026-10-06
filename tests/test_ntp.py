import struct
import unittest
from unittest.mock import patch

from timekeeper import EPOCH, ntp_stamp, query_ntp


class FakeSocket:
    def __init__(self, mode='valid'):
        self.mode = mode
        self.sent = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendto(self, data, address):
        self.sent = bytes(data)
        self.address = address

    def recvfrom(self, size):
        response = bytearray(48)
        response[0] = 0x24
        response[1] = 2
        response[24:32] = self.sent[40:48] if self.mode != 'mismatch' else b'badnonce'
        response[32:40] = ntp_stamp(1000.02)
        response[40:48] = ntp_stamp(1000.03)
        return bytes(response), ('127.0.0.1', 123)

    def close(self):
        pass


class NtpTests(unittest.TestCase):
    def test_timestamp_round_trip(self):
        sec, frac = struct.unpack('!II', ntp_stamp(12345.25))
        self.assertEqual(sec, EPOCH + 12345)
        self.assertEqual(frac, 2**30)

    @patch('timekeeper.time.time', side_effect=[1000.0, 1000.05])
    @patch('timekeeper.socket.socket')
    def test_offset_and_delay(self, factory, clock):
        factory.return_value = FakeSocket()
        result = query_ntp('127.0.0.1')
        self.assertAlmostEqual(result['offset'], 0, places=5)
        self.assertAlmostEqual(result['delay'], 0.04, places=5)
        self.assertEqual(result['stratum'], 2)

    @patch('timekeeper.time.time', side_effect=[1000.0, 1000.05])
    @patch('timekeeper.socket.socket')
    def test_rejects_unmatched_response(self, factory, clock):
        factory.return_value = FakeSocket('mismatch')
        with self.assertRaisesRegex(ValueError, 'Invalid'):
            query_ntp('127.0.0.1')


if __name__ == '__main__':
    unittest.main()
