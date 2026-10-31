# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: MIT-0
"""Frisson's player: library, DAC, keys, menus and screen. code.py imports it
after reserving the audio buffers (see code.py for the board and the order).
"""

import os
import time

import microcontroller

import board
import busio
import pwmio
import sdcardio
import storage

import adafruit_flac
import adafruit_tlv320
import keys as keypad
import library
import menus
from audiobuf import audio, out_buf

LEVEL = 0.5  # FLACPlayer output level, 0.0 to 1.0
DAC_DB = 0  # TLV320 DAC volume at start
HP_DB = -8.1  # TLV320 analog headphone volume, 0 (loud) to -78.3
DAC_DB_MIN = -63  # wheel range
DAC_DB_MAX = 0  # above this the DAC clips; loud masters distorted at 0 dB on headphones (10-27)
MCLK_HZ = 15_000_000
RESTART_S = 3  # reverse after this far into a track restarts it
HIGH_RATE = 48000  # above this, no e-ink redraw for QUIET_S after a track starts:
QUIET_S = 0  # was 6 for screen.py's 0.1 s redraw; fastscreen blocks 13-16 ms, and C decodes faster
SCAN_S = 5  # seconds per rewind / fast-forward step (one step every 0.3 s held)
# What a damaged or unreadable file raises: FLACError is a ValueError, short
# reads give IndexError or struct's RuntimeError, a wild STREAMINFO a MemoryError.
BAD_FILE = (ValueError, OSError, IndexError, RuntimeError, MemoryError)

mclk = pwmio.PWMOut(board.D5, frequency=MCLK_HZ, duty_cycle=32768)

# Read the card through the VfsFat object, so CIRCUITPY needs no /sd folder.
spi = board.SPI()
sd = sdcardio.SDCard(spi, board.D24, baudrate=24_000_000)
vfs = storage.VfsFat(sd)


def first_track():
    """The first path in the library index, or None before it is built."""
    try:
        with vfs.open(library.INDEX, "r") as f:
            return f.readline().split("\t")[0]
    except OSError:
        return None


# The decoder keeps its buffers across files, so open it now, while SRAM is
# free (step 3 above), on the file with the largest blocks and frames (4608
# and 22977 bytes, of all 356 tracks, 10-27). Opened on a smaller one, the
# buffers were reallocated in PSRAM for bigger files.
BIGGEST = "/Aphex_Twin-Selected_Ambient_Works/12-aphex_twin-12.flac"
try:
    path = BIGGEST
    vfs.stat(path)
except OSError:
    path = first_track()
decoder = None
try:
    if path and path[-5:].lower() == ".flac":
        decoder = adafruit_flac.FLACDecoder(vfs.open(path, "rb"))
except BAD_FILE as e:  # opened on the first track that plays instead
    print("no decoder yet:", path, e)
import mp3file  # noqa: E402  after the FLAC buffers, before the library takes the rest
mp3 = mp3file.MP3Decoder()
lib = library.Library(vfs, lambda n, total: n % 50 or print("indexing", n, "of", total))
print(len(lib.tracks), "tracks,", len(lib.albums), "albums")

# 50 kHz: with the NeoKey and ANO on STEMMA QT, 400 kHz misses devices and can
# hang the bus (measured 10-27).
i2c = busio.I2C(board.SCL, board.SDA, frequency=50_000)
# For about a second after the bus starts, transactions fail (no ACK, or
# ETIMEDOUT), then it settles. Wait for 10 good reads of the TLV in a row.
while not i2c.try_lock():
    pass
good = 0
settle_until = time.monotonic() + 3
while good < 10 and time.monotonic() < settle_until:
    try:
        i2c.writeto_then_readfrom(0x18, b"\x00", bytearray(1))
        good += 1
    except OSError:
        good = 0
i2c.unlock()
dac = adafruit_tlv320.TLV320DAC3100(i2c)


# 88.2 and 96 kHz, which the TLV library's MCLK table stops short of:
# PLL 15 MHz * J.D to 64 * 16 * rate, NDAC 2, MDAC 8, DOSR 64. That leaves the
# DAC's DSP MDAC * DOSR = 512 clocks per sample (half rate with DOSR 64 left
# it 64 and played slow and garbled, 10-27). Processing block 8 is filter B,
# for rates above 48 kHz.
HIGH_RATES = {88200: (6, 211), 96000: (6, 5536)}  # J, D


def set_clocks(rate):
    """Set the TLV's clocks for `rate`."""
    if rate not in HIGH_RATES:
        dac.configure_clocks(sample_rate=rate, bit_depth=16, mclk_freq=MCLK_HZ)
        dac._page0._write_register(0x3C, 1)  # processing block 1, filter A
        return
    dac.configure_clocks(sample_rate=rate // 2, bit_depth=16, mclk_freq=MCLK_HZ)  # format, muxes
    j, d = HIGH_RATES[rate]
    p0 = dac._page0
    p0._set_bits(0x3F, 0x03, 6, 0b00)  # DACs off
    p0._write_register(0x05, 0x11)  # PLL off, P 1, R 1
    p0._write_register(0x06, j)
    p0._write_register(0x07, d >> 8)
    p0._write_register(0x08, d & 0xFF)
    p0._write_register(0x05, 0x91)  # PLL on
    time.sleep(0.01)
    p0._write_register(0x0B, 0x82)  # NDAC 2
    p0._write_register(0x0C, 0x88)  # MDAC 8
    p0._write_register(0x0D, 0)  # DOSR 64
    p0._write_register(0x0E, 64)
    p0._write_register(0x3C, 8)
    p0._set_bits(0x3F, 0x03, 6, 0b11)


def output_rate(rate):
    """The sample rate the I2S output really runs at. CircuitPython clocks
    the PIO at 6 x 32 x rate from the CPU clock with a 1/256 divider, rounded
    up (ports/raspberrypi/common-hal/rp2pio/StateMachine.c). At 300 MHz, 96 kHz
    comes out at 95992 Hz, 80 ppm slow. FLACPlayer times the halves from the
    nominal rate, so it drifts 5 ms a minute, and after about a minute of
    Zooropa it wrote halves before the output had copied them: clicks, with
    no underrun counted (10-27)."""
    f256 = microcontroller.cpu.frequency * 256
    want = 6 * 32 * rate
    div = f256 // want
    if f256 % div:  # the same test as StateMachine.c
        div += 1
    return f256 / div / (6 * 32)


class Player:
    """Plays a queue of library tracks. Menus drives it."""

    def __init__(self):
        self.flac = adafruit_flac.FLACPlayer(audio, buffer=out_buf, level=LEVEL)
        self.decoder = decoder  # the current file's decoder
        self.flac_decoder = decoder
        self.file = decoder.file if decoder else None  # closed on the first play
        self.rate = 0
        self.db = DAC_DB
        self.queue = []
        self.pos = 0
        self.current = None  # library track index
        self.started = 0
        self.quiet_until = 0  # no e-ink redraw before this (time.monotonic)
        self.skipped = False  # a damaged file was passed over; Menus.tick reports it

    @property
    def playing(self):
        return self.current is not None and not self.flac.paused

    @property
    def paused(self):
        return self.current is not None and self.flac.paused

    def position(self):
        return self.pos + 1, len(self.queue)

    def play(self, queue, pos, skip=False):
        """Play queue[pos], or with skip the first track from there that can
        play. False if it can't, and what was playing carries on: untouched for
        an MP3, restarted where it was for a file that fails to open."""
        step = 1 if skip else 0
        pos = self._find(queue, pos, step)
        if pos < 0:
            return False
        had = self.current is not None
        old_queue, old_pos, paused = self.queue, self.pos, self.paused
        at = self.decoder.samples_decoded / self.rate if had else 0
        self.queue = queue
        if self._start(pos, step):
            return True
        self.skipped = False  # the caller reports this one
        self.queue, self.pos = old_queue, old_pos
        if had and self._start(old_pos, 0):
            self._seek(at)
            if paused:
                self.flac.pause()
        return False

    def _find(self, queue, pos, step):
        """The first playable index from pos going by step, else -1. With
        step 0, only pos itself."""
        while 0 <= pos < len(queue):
            if menus.playable(lib.tracks[queue[pos]][0]):
                return pos
            if not step:
                break
            pos += step
        return -1

    def _start(self, pos, step=1):
        """Stop, then play from pos, passing over what can't play. False, with
        nothing playing, if nothing can."""
        self.flac.stop()
        if self.file:
            self.file.close()
            self.file = None
        pos = self._find(self.queue, pos, step)
        while pos >= 0:
            if self._load(pos):
                return True
            self.skipped = True
            pos = self._find(self.queue, pos + step, step) if step else -1
        self.current = None
        return False

    def _load(self, pos):
        i = self.queue[pos]
        path = lib.tracks[i][0]
        try:
            self.file = vfs.open(path, "rb")
            if path[-4:].lower() == ".mp3":
                mp3.open(self.file)
                self.decoder = mp3
            elif self.flac_decoder is None:
                self.decoder = self.flac_decoder = adafruit_flac.FLACDecoder(self.file)
            else:
                self.flac_decoder.open(self.file)
                self.decoder = self.flac_decoder
            d = self.decoder
            if d.sample_rate != self.rate:
                self.rate = d.sample_rate
                set_clocks(self.rate)
                dac.headphone_output = True
                dac.speaker_output = False  # class-D amp off, nothing on it
                dac.headphone_volume = HP_DB  # headphone_output sets -20.1
                dac.dac_volume = self.db
            print("%s  %d Hz %d-bit %d:%02d" % (path, self.rate, d.source_bits_per_sample,
                                               d.duration // 60, d.duration % 60))
            self._play()
        except BAD_FILE as e:
            print("can't play", path, e)
            if self.file:
                self.file.close()
                self.file = None
            self.rate = 0  # set the clocks again next time
            return False
        self.pos = pos
        self.current = i
        self.started = time.monotonic()
        self.quiet_until = self.started + QUIET_S if self.rate > HIGH_RATE else 0
        return True

    def _play(self):
        f = self.flac
        f.play(self.decoder)
        # time the halves from the real output rate (see output_rate)
        f._half_ns = int(f._half // self.decoder.channel_count * 1e9 / output_rate(self.rate))

    def pause(self):
        if self.playing:
            self.flac.pause()

    def resume(self):
        if self.paused:
            self.flac.resume()

    def reverse(self):
        if self.current is None:
            return
        previous = self._find(self.queue, self.pos - 1, -1)
        if time.monotonic() - self.started > RESTART_S or previous < 0:
            self._start(self.pos)
        else:
            self._start(previous, -1)

    def forward(self):
        if self.current is not None:
            self._start(self.pos + 1)

    def scan(self, direction):
        """Rewind (-1) or fast-forward (1) one SCAN_S step."""
        if self.current is None:
            return
        self._seek(self.decoder.samples_decoded / self.rate + direction * SCAN_S)

    def _seek(self, seconds):
        try:
            at = self.decoder.seek(max(0, seconds))
            self._play()  # past the end, this plays silence and update() moves on
            self.started = time.monotonic() - at  # so reverse() still restarts the track
        except BAD_FILE as e:
            self._bad_file(e)

    def _bad_file(self, e):
        print("can't play on:", e)
        self.skipped = True
        self._start(self.pos + 1)

    def volume(self, steps):
        self.db = max(DAC_DB_MIN, min(DAC_DB_MAX, self.db + steps))
        dac.dac_volume = self.db

    def update(self):
        """Keep the audio fed. Returns True when a track just ended."""
        if self.current is None:
            return False
        try:
            self.flac.update()
        except BAD_FILE as e:
            self._bad_file(e)
            return True
        if not self.flac.playing and not self.flac.paused:
            print("  done, underruns", self.flac.underruns)
            self._start(self.pos + 1)
            return True
        return False


player = Player()
ui = menus.Menus(lib, player)

try:
    keys = keypad.Keys(i2c)
    print("NeoKey", "found" if keys.neokey else "missing", " ANO", "found" if keys.ano else "missing")
except ValueError as e:  # neither NeoKey nor ANO plugged in
    print("no keys:", e)
    keys = None

try:
    import fastscreen as screen  # noqa: PLC0415  (screen.py: the 4-gray displayio one)

    eink = screen.EinkScreen(spi)
except (ImportError, ValueError, RuntimeError) as e:
    print("no screen:", e)
    eink = None

start = os.getenv("FRISSON_PLAY")  # settings.toml: a track to play at boot, for testing
for album in lib.albums if start else ():
    queue = album[2]
    for n, i in enumerate(queue):
        if lib.tracks[i][0] == start:
            player.play(queue, n)
            ui.now_playing()
if player.current is None and (keys is None or keys.neokey is None):
    player.play(lib.albums[0][2], 0, True)  # nothing to choose with: play the first album
    ui.now_playing()

printed = -1
last = time.monotonic_ns()
while True:
    if player.update():
        ui.version += 1  # next track: the footer and now playing change
    ui.tick()
    if keys:
        events = keys.poll()
        for kind, value in events:
            ui.handle(kind, value)
        if events and eink:
            eink.poke()
    if ui.version != printed:
        printed = ui.version
        print(ui.text())
    if eink and time.monotonic() >= player.quiet_until:
        eink.update(ui)
    t = time.monotonic_ns()
    if t - last >= 30_000_000_000:
        print("  underruns", player.flac.underruns)
        last = t
