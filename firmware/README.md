# Firmware

Frisson plays the `.flac` files on its microSD card through the TLV320DAC3100 to the headphone jack, with menus on the 2.13" e-ink screen. Tested on CircuitPython 11.0.0-alpha.2 on the Feather RP2350, at 300 MHz.

## Before you start: the MCLK jumper

Solder a wire from **TP1** (Feather D5) to **TP2** (TLV320 MCLK). Both are large pads labelled `JMP` on the board. TP2 sits under the TLV320 breakout, so solder the wire before you plug the breakout in.

D5 sends a 15 MHz clock to the TLV320's MCLK pin. The TLV320's PLL locks to it. Without the jumper, the PLL has to run from the I2S bit clock, and the audio sounds muffled.

## Pins

| Function | Feather pin |
|---|---|
| I2S BCLK | A0 |
| I2S WSEL | A1 |
| I2S DIN | A2 |
| TLV320 MCLK (15 MHz PWM, over the jumper) | D5 |
| TLV320 control (I2C) | SCL, SDA |
| microSD (on the EYESPI breakout) | SCK, MOSI, MISO, CS on D24 |
| E-ink (Adafruit 6383, SSD1680) | SCK, MOSI, CS D25, DC RX, RST TX, BUSY A3 |
| NeoKey 1x4 (0x30) and ANO (0x49) | STEMMA QT (SCL, SDA) |

## Setup

1. Install the libraries: `circup install adafruit_tlv320 adafruit_seesaw`
2. Copy `code.py`, `fonts/` and everything in `lib/` to the CIRCUITPY drive. `lib/adafruit_flac/` is a build of [Adafruit_CircuitPython_FLAC](https://github.com/adafruit/Adafruit_CircuitPython_FLAC) with `seek()` added, for rewind and fast-forward, and `flac_native`, a C decode kernel that loads on stock firmware and falls back to viper. Use it in place of the released library.
3. Put your music on the card, one folder per album.

The first start reads every file's tags into `/FRISSON.IDX` on the card. Delete it after changing the music.

For testing, `FRISSON_PLAY = "/Album/01 - Track.flac"` in `settings.toml` plays that track at boot.

## Files

| File | What it does |
|---|---|
| `code.py` | Sets 300 MHz and reserves the audio buffers in SRAM, then imports the player |
| `lib/audiobuf.py` | The I2S output and its buffer |
| `lib/frisson.py` | The player: SD card, DAC clocks, playback, the main loop |
| `lib/library.py` | Reads FLAC tags and keeps the index |
| `lib/menus.py` | What every key does, and what the screen shows |
| `lib/keys.py` | NeoKey and ANO input, one seesaw read per loop pass |
| `lib/fastscreen.py` | The e-ink driver, with fast partial updates |
| `lib/screen.py` | The older 4-gray displayio driver (5.6 s a refresh) |
| `tools/draw_menus.py` | Draws the menus as PNGs on a computer |

## Controls

| Input | Action |
|---|---|
| ANO wheel | Move the highlight (volume on the now playing screen) |
| ANO select | Open the album, or play the track |
| ANO up / down | Back / now playing |
| ANO left / right | Page down / page up |
| NeoKey 1 to 4 | Play, pause, reverse, forward |
| Hold NeoKey 3 / 4 | Rewind / fast-forward, 5 s a step |

Reverse restarts the track, or goes to the previous one in its first 3 seconds.

## The e-ink screen

`lib/fastscreen.py` drives the SSD1680 directly instead of through displayio:

| | displayio (`screen.py`) | `fastscreen.py` |
|---|---|---|
| Update | Full 4-gray waveform, flashes | Partial waveform, changed pixels only |
| Panel between updates | Reset and deep sleep | Stays awake |
| Main loop held per update | about 100 ms | two 4000-byte SPI writes and the drawing |
| Colors | 4 grays | Black and white |

A full refresh clears ghosting after 40 partial updates, or once the screen has been still for 30 s. Text is Terminus 18 bold, read from the BDF and stored already rotated into the panel's order, so drawing is only `bitmaptools` blits.

## 24-bit / 96 kHz

The TLV320 library's clock table stops at 48 kHz. `set_clocks()` in `lib/frisson.py` sets the PLL and dividers for 88.2 and 96 kHz itself. Output is 16-bit.

24/96 only keeps up when the decoder and the audio buffers are in internal SRAM, not PSRAM, so `code.py` loads them in this order:

1. `adafruit_flac`, so the decode kernel is in SRAM
2. the output buffer and the I2S DMA buffers
3. the decoder, opened on the largest file
4. everything else

`adafruit_flac` must be the `.mpy`. As a `.py` it is compiled on the board, and that leaves too little SRAM for the DMA buffers (`RuntimeError: Unable to allocate buffers for signed conversion`).
