/*******************************************************************************
 * File Name        : jpeg_encoder_yuyv.h
 *
 * Description      : JPEG encoding of YUYV (YUV 4:2:2) camera frames using a
 *                    static JPEGENC instance. Adapted from the Face ID demo's
 *                    jpeg_encoder wrapper (which encoded RGB565); the UVC
 *                    camera delivers YUYV natively and JPEGENC consumes it
 *                    directly, so no color conversion pass is needed.
 *******************************************************************************/

#ifndef JPEG_ENCODER_YUYV_H
#define JPEG_ENCODER_YUYV_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Quality levels map to JPEGE_Q_BEST..JPEGE_Q_LOW (0=best, 3=low). */
#define JPEG_QUALITY_BEST    (0u)
#define JPEG_QUALITY_HIGH    (1u)
#define JPEG_QUALITY_MEDIUM  (2u)
#define JPEG_QUALITY_LOW     (3u)

/*******************************************************************************
* Encodes one YUYV 4:2:2 frame (width*height*2 bytes) into out_buf.
* Returns the compressed size in bytes (> 0), or a negative error code
* (-1 bad args, -2 open, -3 begin, -4 frame, -5 close/overflow).
*******************************************************************************/
int jpeg_encode_yuyv(const uint8_t *yuyv,
                     uint16_t width, uint16_t height,
                     uint8_t quality,
                     uint8_t *out_buf, uint32_t out_cap);

#ifdef __cplusplus
}
#endif

#endif /* JPEG_ENCODER_YUYV_H */
