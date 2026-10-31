// SPDX-FileCopyrightText: 2026 Mikey Sklar
// SPDX-License-Identifier: MIT-0
#pragma once
#include <stddef.h>
void *mp3_alloc(size_t sz);
#define MPDEC_ALLOCATOR(x) mp3_alloc(x)
#define MPDEC_FREE(x)
