# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: MIT-0
"""MP3 files for FLACPlayer: the parts of FLACDecoder it and the Player use.

mp3_native only decodes from a buffer. The SD reads happen here, in the main
loop, because sdcardio fails a read when the bus is taken, and the e-ink shares
the bus: audiomp3 reads in the background and stopped for good on the first
collision (10-31).

Durations and seeks come from the first frame's bit rate, so on a VBR file they
are estimates.
"""

from array import array

import mp3_native

_MAX_TRIES = 300  # bad frames in a row before giving up on a file
_REFILL = 2048  # more than the longest frame, 1441 bytes
_ARENA = bytearray(40000)  # mp3_native keeps its state here for good
mp3_native.init(_ARENA)


class MP3Decoder:
    def __init__(self):
        self._in = bytearray(8192)
        self._in_mv = memoryview(self._in)
        self._pcm = array("h", [0]) * 2304
        self._pcm_mv = memoryview(self._pcm)
        self._info = array("i", [0]) * 5
        self.file = None
        self.sample_rate = 0
        self.channel_count = 0
        self.source_bits_per_sample = 16
        self.bit_rate = 0  # the first frame's
        self.duration = 0
        self.samples_decoded = 0
        self._start = 0
        self._size = 0
        self._s = 0
        self._e = 0
        self._eof = False
        self._n = 0  # samples in _pcm
        self._p = 0  # next one to hand out

    def open(self, file):
        """Start on `file`. ValueError if it holds no MP3 audio."""
        self.file = file
        file.seek(0, 2)
        self._size = file.tell()
        self._start = self._after_id3()
        self.rewind()
        if not self._frame():
            raise ValueError("no MP3 frames")
        info = self._info
        self.sample_rate = info[2]
        self.channel_count = info[3]
        self.bit_rate = info[4]
        bytes_per_s = info[4] // 8 or 1
        self.duration = (self._size - self._start) // bytes_per_s

    def _after_id3(self):
        # An ID3v2 tag can hold megabytes of cover art: seek past it.
        f = self.file
        f.seek(0)
        h = bytearray(10)
        if f.readinto(h) == 10 and h[:3] == b"ID3":
            n = h[6] << 21 | h[7] << 14 | h[8] << 7 | h[9]
            return n + (20 if h[5] & 0x10 else 10)  # 0x10: a footer follows
        return 0

    def rewind(self):
        self.file.seek(self._start)
        self._reset(0)

    def _reset(self, decoded):
        self._s = self._e = 0
        self._eof = False
        self._n = self._p = 0
        self.samples_decoded = decoded

    def seek(self, seconds):
        """Jump to about `seconds`. Returns the time it landed on."""
        if seconds <= 0 or not self.duration:
            self.rewind()
            return 0
        if seconds >= self.duration:
            self.file.seek(self._size)
            self._reset(self.duration * self.sample_rate)
            self._eof = True
            return self.duration
        audio = self._size - self._start
        self.file.seek(self._start + int(audio * (seconds / self.duration)))
        self._reset(int(seconds * self.sample_rate))
        return seconds

    def _frame(self):
        # Decode the next frame into _pcm. False at the end of the file.
        d = self._in
        info = self._info
        for _ in range(_MAX_TRIES):
            if self._e - self._s < _REFILL and not self._eof:
                left = self._e - self._s
                self._in_mv[:left] = self._in_mv[self._s : self._e]
                self._s = 0
                r = self.file.readinto(self._in_mv[left:])
                self._e = left + (r or 0)
                self._eof = not r
            i = mp3_native.find_sync(d, self._s, self._e)
            if i < 0:
                self._s = self._e
                if self._eof:
                    return False
                continue
            if mp3_native.decode(d, i, self._e, self._pcm, info):
                # Bad data, or the bit reservoir after a seek: try the next sync.
                if self._eof and self._e - i < _REFILL:
                    self._s = i + 1
                    if self._e - self._s < 4:
                        return False
                else:
                    self._s = info[0] if info[0] > i else i + 1
                continue
            self._s = info[0]
            self._n = info[1]
            self._p = 0
            self.samples_decoded += info[1] // info[3]
            return True
        raise ValueError("bad MP3 data")

    def fill(self, mv):
        """Copy decoded samples into mv, decoding at most one frame. Returns
        the number of values written, 0 at the end of the file."""
        if self._p >= self._n and not self._frame():
            return 0
        p = self._p
        n = min(self._n - p, len(mv))
        mv[:n] = self._pcm_mv[p : p + n]
        self._p = p + n
        return n
