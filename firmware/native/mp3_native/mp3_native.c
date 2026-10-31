// SPDX-FileCopyrightText: 2026 Mikey Sklar
// SPDX-License-Identifier: MIT-0
//
// mp3_native: the Helix fixed-point MP3 decoder that CircuitPython's audiomp3 uses, as a native
// module. It only decodes from a buffer, so Python does the SD reads between screen updates:
// sdcardio fails a read when the bus is taken, and audiomp3 reads in the background.
//
// Build: make MPY_DIR=path/to/circuitpython MP3_SRC=path/to/circuitpython/lib/mp3/src

#include "py/dynruntime.h"
#include "mp3dec.h"

#define ARENA_BYTES 40000

// Not static: a native module's bss can't be. The arena is a bytearray from init(); the caller
// must keep it for as long as it decodes, so the GC keeps it too.
HMP3Decoder decoder;
uint8_t *arena;
size_t arena_used;

// The decoder's structs, carved from the arena, MPDEC_ALLOCATOR in mp3_alloc.h.
void *mp3_alloc(size_t sz) {
    sz = (sz + 7) & ~(size_t)7; // bytearrays are at least 8-byte aligned on the GC heap
    if (arena_used + sz > ARENA_BYTES) {
        return NULL;
    }
    void *p = arena + arena_used;
    arena_used += sz;
    return p;
}

static inline void *buf_w(mp_obj_t o) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(o, &bi, MP_BUFFER_RW);
    return bi.buf;
}

// init(buf): start a decoder in buf, a bytearray of at least ARENA_BYTES.
static mp_obj_t init(mp_obj_t buf_in) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(buf_in, &bi, MP_BUFFER_RW);
    if (bi.len < ARENA_BYTES) {
        mp_raise_ValueError(MP_ERROR_TEXT("arena too small"));
    }
    arena = (uint8_t *)bi.buf;
    arena_used = 0;
    decoder = MP3InitDecoder();
    if (!decoder) {
        mp_raise_ValueError(MP_ERROR_TEXT("arena too small"));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(init_obj, init);

// find_sync(buf, start, end): index of the next frame sync in buf[start:end], or -1.
static mp_obj_t find_sync(mp_obj_t buf_in, mp_obj_t start_in, mp_obj_t end_in) {
    uint8_t *buf = (uint8_t *)buf_w(buf_in);
    int start = mp_obj_get_int(start_in);
    int off = MP3FindSyncWord(buf + start, mp_obj_get_int(end_in) - start);
    return mp_obj_new_int(off < 0 ? -1 : start + off);
}
static MP_DEFINE_CONST_FUN_OBJ_3(find_sync_obj, find_sync);

// decode(buf, start, end, pcm, info): one frame from buf[start:end] into the int16 array pcm.
// Returns the Helix error, 0 for success. info is an int32 array that gets the index after the
// frame, then samples written (all channels), sample rate, channels and bitrate.
static mp_obj_t decode(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    uint8_t *buf = (uint8_t *)buf_w(args[0]);
    int start = mp_obj_get_int(args[1]);
    int left = mp_obj_get_int(args[2]) - start;
    int32_t *info = (int32_t *)buf_w(args[4]);
    unsigned char *p = buf + start;
    int err = MP3Decode(decoder, &p, &left, (short *)buf_w(args[3]), 0);
    info[0] = p - buf;
    if (err == 0) {
        MP3FrameInfo fi;
        MP3GetLastFrameInfo(decoder, &fi);
        info[1] = fi.outputSamps;
        info[2] = fi.samprate;
        info[3] = fi.nChans;
        info[4] = fi.bitrate;
    }
    return mp_obj_new_int(err);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(decode_obj, 5, 5, decode);

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY
    mp_store_global(MP_QSTR_init, MP_OBJ_FROM_PTR(&init_obj));
    mp_store_global(MP_QSTR_find_sync, MP_OBJ_FROM_PTR(&find_sync_obj));
    mp_store_global(MP_QSTR_decode, MP_OBJ_FROM_PTR(&decode_obj));
    MP_DYNRUNTIME_INIT_EXIT
}
