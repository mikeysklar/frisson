# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: MIT-0
# mp3_native.mpy for the RP2350. MP3_SRC is CircuitPython's lib/mp3/src, the Helix decoder.
MPY_DIR ?= ../../../../../../Volumes/clear/Downloads/ada/circuitpython
MP3_SRC ?= /Volumes/clear/Downloads/ada/cp-main/lib/mp3/src
MOD = mp3_native
ARCH ?= armv7emsp
HELIX = bitstream buffers dct32 dequant dqchan huffman hufftabs imdct mp3dec mp3tabs polyphase \
	scalfact stproc subband trigtabs
$(shell ln -sfn $(MP3_SRC) helix)
SRC = mp3_native.c $(addprefix helix/,$(addsuffix .c,$(HELIX)))
LINK_RUNTIME = 1
CFLAGS_EXTRA = -O2 -fwrapv -w -Ihelix -include mp3_alloc.h
export PYTHONHASHSEED = 0

include $(MPY_DIR)/py/dynruntime.mk
