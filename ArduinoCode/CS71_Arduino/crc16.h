#ifndef CS71_CRC16_H
#define CS71_CRC16_H

#include <stddef.h>
#include <stdint.h>

const uint16_t CRC16_CCITT_FALSE_INITIAL = 0xFFFFU;

uint16_t crc16CcittFalseUpdateByte(uint16_t crc, uint8_t byte);
uint16_t crc16CcittFalseUpdate(uint16_t crc, const uint8_t *bytes,
                                size_t length);
uint16_t crc16CcittFalse(const uint8_t *bytes, size_t length);

bool formatCrc16CcittFalse(char *output, size_t capacity, uint16_t crc);
bool parseCrc16CcittFalse(const char *text, size_t length, uint16_t *crc);

#endif
