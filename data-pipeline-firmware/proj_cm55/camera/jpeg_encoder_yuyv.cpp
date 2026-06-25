/*******************************************************************************
 * File Name        : jpeg_encoder_yuyv.cpp
 *
 * Description      : See jpeg_encoder_yuyv.h. Static encoder instance keeps
 *                    the ~20 KB JPEGENC state off the task stack.
 *******************************************************************************/

#include "jpeg_encoder_yuyv.h"
#include "JPEGENC.h"

static JPEGENC g_jpeg_encoder;

extern "C" int jpeg_encode_yuyv(const uint8_t *yuyv,
                                uint16_t width, uint16_t height,
                                uint8_t quality,
                                uint8_t *out_buf, uint32_t out_cap)
{
    if ((yuyv == NULL) || (out_buf == NULL) || (out_cap < 1024u))
    {
        return -1;
    }
    if (quality > JPEGE_Q_LOW)
    {
        quality = JPEGE_Q_LOW;
    }

    if (g_jpeg_encoder.open(out_buf, (int)out_cap) != JPEGE_SUCCESS)
    {
        return -2;
    }

    JPEGENCODE ctx;
    /* YUV422 input is already in the encoder's native color space; 4:2:0
     * subsampling keeps the output small for the Wi-Fi preview stream. */
    if (g_jpeg_encoder.encodeBegin(&ctx, width, height,
                                   JPEGE_PIXEL_YUV422,
                                   JPEGE_SUBSAMPLE_420,
                                   quality) != JPEGE_SUCCESS)
    {
        return -3;
    }

    if (g_jpeg_encoder.addFrame(&ctx, (uint8_t *)yuyv,
                                (int)width * 2) != JPEGE_SUCCESS)
    {
        return -4;
    }

    int jpeg_size = g_jpeg_encoder.close();
    if ((jpeg_size <= 0) || (jpeg_size > (int)out_cap))
    {
        return -5;
    }
    return jpeg_size;
}
