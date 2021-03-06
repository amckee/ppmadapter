# PPMAdapter - An RC PPM decoder and joystick emulator
# Copyright 2016 Nigel Sim <nigel.sim@gmail.com>
#
# PPMAdapter is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PPMAdapter is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PPMAdapter.  If not, see <http://www.gnu.org/licenses/>.

import pyaudio
import argparse
import sys
from evdev import UInput, ecodes
import array
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll
from contextlib import contextmanager

# Suppress ALSA errors
# http://stackoverflow.com/questions/7088672
ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)


def py_error_handler(filename, line, function, err, fmt):
    pass

c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)


@contextmanager
def noalsaerr():
    asound = cdll.LoadLibrary('libasound.so')
    asound.snd_lib_error_set_handler(c_error_handler)
    yield
    asound.snd_lib_error_set_handler(None)


class PPMDecoder(object):
    """Decodes the audio data into PPM pulse data, and then into uinput
    joystick events.
    """
    def __init__(self, rate):
        """
        Parameters
        ----------
        rate : int
            sample rate
        """
        self._rate = float(rate)
        self._lf = None
        self._threshold = 15000
        self._last_edge = None
        self._ch = None

        # Size in sampling intervals, of the frame space marker
        self._marker = int(2.0 * 0.0025 * self._rate)

        # Mapping of channels to events
        self._mapping = {0: ecodes.ABS_X,
                         1: ecodes.ABS_Y,
                         2: ecodes.ABS_Z,
                         3: ecodes.ABS_THROTTLE}

        events = [(v, (0, 5, 255, 0)) for v in self._mapping.values()]

        self._ev = UInput(name='ppmadapter',
                          events={
                               ecodes.EV_ABS: events,
                               ecodes.EV_KEY: {288: 'BTN_JOYSTICK'}
                          })

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self._ev.close()

    def feed(self, data):
        """Feeds the decoder with a block of sample data.

        The data should be integer values, and should only be a single channel.

        Parameters
        ----------
        data : list
            sample data
        """
        sync_req = False
        for i in range(len(data)):
            this_edge = data[i] > self._threshold
            if self._last_edge is None:
                self._last_edge = this_edge
                continue

            if this_edge and not self._last_edge:
                # rising
                if self._lf is not None:
                    sync_req |= self.signal(i - self._lf)
            elif not this_edge and self._last_edge:
                # falling
                self._lf = i

            self._last_edge = this_edge

        if sync_req:
            self._ev.syn()

        if self._lf is not None:
            self._lf = self._lf - len(data)
            if self._lf < (-self._rate):
                print("Lost sync")
                self._ch = None
                self._lf = None

    def signal(self, w):
        """Process the detected signal.

        The signal is the number of sampling intervals between the falling
        edge and the rising edge.

        Parameters
        ----------
        w : int
            signal width

        Returns
        -------
        bool
            does uinput require sync
        """
        if w > self._marker:
            if self._ch is None:
                print("Got sync")
            self._ch = 0
            return False

        if self._ch is None or self._ch not in self._mapping:
            return False

        duration = float(w) / self._rate
        value = int((duration - 0.0007) * 1000 * 255)
        self._ev.write(ecodes.EV_ABS, self._mapping[self._ch], value)

        self._ch += 1

        return True


def print_inputs():
    with noalsaerr():
        print("Input audio devices")
        print("-------------------")
        a = pyaudio.PyAudio()
        for i in range(a.get_device_count()):
            d = a.get_device_info_by_index(i)
            print( "%s: \t Max Channels: in[%s] out[%s]" % (d['name'], d['maxInputChannels'], d['maxOutputChannels']) )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', help="input audio device name", default='default')
    parser.add_argument('action', default='run', choices=['run', 'inputs'])

    args = parser.parse_args()

    if args.action == 'inputs':
        print_inputs()
        return 0

    in_ix = None
    rate = None
    in_name = None
    with noalsaerr():
        a = pyaudio.PyAudio()
    for i in range(a.get_device_count()):
        d = a.get_device_info_by_index(i)
        if args.i == d['name']:
            in_ix = d['index']
            rate = int(d['defaultSampleRate'])
            in_name = d['name']
            break
        if args.i in d['name']:
            in_ix = d['index']
            rate = int(d['defaultSampleRate'])
            in_name = d['name']

    print("Using input: %s" % in_name)

    chunk = 2048

    stream = a.open(format=pyaudio.paInt16,
                    channels=1,
                    rate=rate,
                    input=True,
                    frames_per_buffer=chunk*2,
                    input_device_index=in_ix)

    try:
        with PPMDecoder(rate) as ppm:
            while True:
                sample = stream.read(chunk)
                sample = array.array('h', sample)
                ppm.feed(sample)
    finally:
        stream.close()

if __name__ == '__main__':
    sys.exit(main())
