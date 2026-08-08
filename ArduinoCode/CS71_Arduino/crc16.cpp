#include "crc16.h"

uint16_t crc16CcittFalseUpdateByte(uint16_t crc, uint8_t byte) {
  crc ^= static_cast<uint16_t>(byte) << 8;
  for (uint8_t bit = 0; bit < 8; ++bit) {
    if ((crc & 0x8000U) != 0U) {
      crc = static_cast<uint16_t>((crc << 1) ^ 0x1021U);
    } else {
      crc = static_cast<uint16_t>(crc << 1);
    }
  }
  return crc;
}

uint16_t crc16CcittFalseUpdate(uint16_t crc, const uint8_t *bytes,
                                size_t length) {
  for (size_t index = 0; index < length; ++index) {
    crc = crc16CcittFalseUpdateByte(crc, bytes[index]);
  }
  return crc;
}

uint16_t crc16CcittFalse(const uint8_t *bytes, size_t length) {
  return crc16CcittFalseUpdate(CRC16_CCITT_FALSE_INITIAL, bytes, length);
}

bool formatCrc16CcittFalse(char *output, size_t capacity, uint16_t crc) {
  if (output == 0 || capacity < 5U) {
    return false;
  }

  const char hex[] = "0123456789ABCDEF";
  output[0] = hex[(crc >> 12) & 0x0FU];
  output[1] = hex[(crc >> 8) & 0x0FU];
  output[2] = hex[(crc >> 4) & 0x0FU];
  output[3] = hex[crc & 0x0FU];
  output[4] = '\0';
  return true;
}

bool parseCrc16CcittFalse(const char *text, size_t length, uint16_t *crc) {
  if (text == 0 || crc == 0 || length != 4U) {
    return false;
  }

  uint16_t parsed = 0;
  for (size_t index = 0; index < length; ++index) {
    const char character = text[index];
    uint8_t nibble;
    if (character >= '0' && character <= '9') {
      nibble = static_cast<uint8_t>(character - '0');
    } else if (character >= 'A' && character <= 'F') {
      nibble = static_cast<uint8_t>(character - 'A' + 10);
    } else {
      return false;
    }
    parsed = static_cast<uint16_t>((parsed << 4) | nibble);
  }

  *crc = parsed;
  return true;
}
