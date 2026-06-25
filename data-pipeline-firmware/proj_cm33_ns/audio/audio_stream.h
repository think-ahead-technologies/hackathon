/*******************************************************************************
 * File Name        : audio_stream.h
 *
 * Description      : Captures the KIT_PSE84_AI on-board stereo PDM microphones
 *                    (XENSIV IM73D122V01, P8_5 clock / P8_6 data on the MXPDM
 *                    block PDM0) and forwards 16 kHz / 16-bit interleaved PCM
 *                    to the Wi-Fi TCP client as type-0x40 frames.
 *
 *  Unlike the camera (captured on the CM55 and handed over through SOCMEM),
 *  the PDM block is owned by this CM33 non-secure core, so no cross-core
 *  hand-off is involved: a PDM interrupt drains the hardware FIFO into a
 *  lock-free ring buffer, and a forwarder task ships fixed-size chunks over
 *  TCP whenever a client is connected (a live feed, like the camera preview).
 *
 *  Wire protocol: audio frame type (alongside 0x10 IMU / 0x20 STATUS /
 *  0x30 CAMERA). Payload layout (little-endian):
 *      uint32  seq           running chunk index (lets the host spot gaps)
 *      uint16  sample_rate   samples/sec per channel (16000)
 *      uint8   channels      interleaved channel count (2 = stereo L,R)
 *      uint8   bits          bits per sample (16)
 *      int16[] pcm           interleaved signed PCM (L,R,L,R,...)
 *******************************************************************************/

#ifndef AUDIO_STREAM_H
#define AUDIO_STREAM_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AUDIO_FRAME_TYPE        (0x40u)
#define AUDIO_FRAME_HDR_LEN     (8u)

/* Creates the PDM capture + forwarder task. Call before the scheduler starts. */
bool audio_stream_create_task(void);

#ifdef __cplusplus
}
#endif

#endif /* AUDIO_STREAM_H */
