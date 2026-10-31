# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: MIT-0
"""The NeoKey 1x4 and the ANO rotary encoder, read over I2C.

poll() returns a list of events, usually empty:
  ("key", n)       NeoKey n (0 to 3) tapped
  ("hold", n)      NeoKey 2 or 3 held: once after HOLD s, then every REPEAT s
  ("button", name) ANO "select", "up", "down", "left" or "right" pressed
  ("wheel", steps) the ANO wheel turned

Keys 0 and 1 send on press. Keys 2 and 3 send on release, so a hold can
replace the tap.

Each seesaw read sleeps read_delay and costs a few hundred microseconds on the
bus, time the FLAC decoder needs at 24/96. So each poll() does one read, in
turn: the NeoKey, the wheel, the NeoKey, the ANO buttons. The NeoKey gets every
other read, so a quick tap's press and release are both seen. The wheel's count
keeps adding up between reads, so no steps are lost.

Libraries: adafruit_seesaw, adafruit_bus_device.
"""

import time

HOLD = 0.5  # NeoKey 2 / 3 hold before rewind / fast-forward
REPEAT = 0.3  # then one ("hold", n) this often
HOLD_KEYS = (2, 3)

_NEOKEY_PINS = 0xF0  # keys 0 to 3 on pins 4 to 7
_ANO_BUTTONS = ((1 << 1, "select"), (1 << 2, "up"), (1 << 3, "left"), (1 << 4, "down"),
                (1 << 5, "right"))
_ANO_PINS = 0x3E
_ENCODER_BASE = 0x11
_ENCODER_DELTA = 0x40
_TURNS = (0, 2, 0, 1)  # NeoKey, wheel, NeoKey, ANO buttons


class Keys:
    """Either board may be missing (`neokey` or `ano` is then None); with
    neither, ValueError."""

    def __init__(self, i2c, neokey_addr=0x30, ano_addr=0x49, interval=0.01, read_delay=0.001):
        from adafruit_seesaw.seesaw import Seesaw  # noqa: PLC0415

        self.interval = interval
        # seesaw sleeps 8 ms per read by default, too long next to FLAC decode
        self.read_delay = read_delay
        self.neokey = self._open(Seesaw, i2c, neokey_addr, _NEOKEY_PINS)
        self.ano = self._open(Seesaw, i2c, ano_addr, _ANO_PINS)
        if self.neokey is None and self.ano is None:
            raise ValueError("no NeoKey at 0x%02x or ANO at 0x%02x" % (neokey_addr, ano_addr))
        self._turn = 0
        self._next = 0.0
        self._keys = 0  # NeoKey bits down, 0 to 3
        self._down_at = [0.0] * 4
        self._hold_next = [0.0] * 4
        self._held = [False] * 4
        self._ano = 0
        self._buf = bytearray(4)

    @staticmethod
    def _open(seesaw, i2c, addr, pins):
        try:
            board = seesaw(i2c, addr)
        except ValueError:  # not plugged in
            return None
        board.pin_mode_bulk(pins, seesaw.INPUT_PULLUP)
        return board

    def poll(self):
        now = time.monotonic()
        if now < self._next:
            return []
        self._next = now + self.interval
        events = []
        turn = _TURNS[self._turn]
        self._turn = (self._turn + 1) % len(_TURNS)
        if turn == 0 and self.neokey:
            # Buttons pull up, so a pressed key reads 0.
            down = (~self.neokey.digital_read_bulk(_NEOKEY_PINS, delay=self.read_delay)
                    & _NEOKEY_PINS) >> 4
            self._neokey(down, now, events)
        elif turn == 1 and self.ano:
            ano = ~self.ano.digital_read_bulk(_ANO_PINS, delay=self.read_delay) & _ANO_PINS
            pressed = ano & ~self._ano
            self._ano = ano
            for bit, name in _ANO_BUTTONS:
                if pressed & bit:
                    events.append(("button", name))
        elif turn == 2 and self.ano:
            self.ano.read(_ENCODER_BASE, _ENCODER_DELTA, self._buf, delay=self.read_delay)
            steps = int.from_bytes(self._buf, "big")
            if steps & 0x80000000:
                steps -= 1 << 32
            if steps:
                events.append(("wheel", steps))
        # held keys keep repeating between NeoKey reads
        for n in HOLD_KEYS:
            if self._keys & (1 << n) and now - self._down_at[n] >= HOLD and now >= self._hold_next[n]:
                events.append(("hold", n))
                self._held[n] = True
                self._hold_next[n] = now + REPEAT
        return events

    def _neokey(self, down, now, events):
        changed = down ^ self._keys
        self._keys = down
        for n in range(4):
            bit = 1 << n
            if not changed & bit:
                continue
            if down & bit:  # pressed
                self._down_at[n] = now
                self._held[n] = False
                if n not in HOLD_KEYS:
                    events.append(("key", n))
            elif n in HOLD_KEYS and not self._held[n]:  # released, not held
                events.append(("key", n))
