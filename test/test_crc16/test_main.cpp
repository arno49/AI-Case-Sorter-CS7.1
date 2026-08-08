#include <string.h>

#include <unity.h>

#include "crc16.h"

static uint16_t crcOfText(const char *text) {
  return crc16CcittFalse(reinterpret_cast<const uint8_t *>(text), strlen(text));
}

void test_published_known_answer_vectors() {
  TEST_ASSERT_EQUAL_HEX16(0xFFFF, crc16CcittFalse(0, 0));
  TEST_ASSERT_EQUAL_HEX16(0x29B1, crcOfText("123456789"));

  TEST_ASSERT_EQUAL_HEX16(0xCF68, crcOfText("@1 done:crc=on"));
  TEST_ASSERT_EQUAL_HEX16(0xD690, crcOfText("@2 crc:off"));
  TEST_ASSERT_EQUAL_HEX16(0x48C9, crcOfText("@2 done:crc=off"));
  TEST_ASSERT_EQUAL_HEX16(0x452B, crcOfText("!16 reject:1002:bad_crc"));
}

void test_incremental_matches_one_shot_for_binary_bytes() {
  const uint8_t bytes[] = {0x00, 0x80, 0xFF, 0x31, 0x00, 0x7F};
  uint16_t incremental = CRC16_CCITT_FALSE_INITIAL;
  for (size_t index = 0; index < sizeof(bytes); ++index) {
    incremental = crc16CcittFalseUpdateByte(incremental, bytes[index]);
  }

  TEST_ASSERT_EQUAL_HEX16(crc16CcittFalse(bytes, sizeof(bytes)), incremental);
  TEST_ASSERT_EQUAL_HEX16(incremental,
                          crc16CcittFalseUpdate(
                              CRC16_CCITT_FALSE_INITIAL, bytes, sizeof(bytes)));
}

void test_crc_formatting_is_uppercase_and_bounded() {
  char formatted[5];
  TEST_ASSERT_TRUE(formatCrc16CcittFalse(formatted, sizeof(formatted), 0x0A1F));
  TEST_ASSERT_EQUAL_STRING("0A1F", formatted);

  char tooSmall[4] = {'x', 'x', 'x', '\0'};
  TEST_ASSERT_FALSE(formatCrc16CcittFalse(tooSmall, sizeof(tooSmall), 0x29B1));
  TEST_ASSERT_FALSE(formatCrc16CcittFalse(0, 5, 0x29B1));
}

void test_crc_parsing_is_strict() {
  uint16_t crc = 0;
  TEST_ASSERT_TRUE(parseCrc16CcittFalse("29B1", 4, &crc));
  TEST_ASSERT_EQUAL_HEX16(0x29B1, crc);

  TEST_ASSERT_FALSE(parseCrc16CcittFalse("29b1", 4, &crc));
  TEST_ASSERT_FALSE(parseCrc16CcittFalse("29B", 3, &crc));
  TEST_ASSERT_FALSE(parseCrc16CcittFalse("29B10", 5, &crc));
  TEST_ASSERT_FALSE(parseCrc16CcittFalse("29G1", 4, &crc));
  TEST_ASSERT_FALSE(parseCrc16CcittFalse(0, 4, &crc));
  TEST_ASSERT_FALSE(parseCrc16CcittFalse("29B1", 4, 0));
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_published_known_answer_vectors);
  RUN_TEST(test_incremental_matches_one_shot_for_binary_bytes);
  RUN_TEST(test_crc_formatting_is_uppercase_and_bounded);
  RUN_TEST(test_crc_parsing_is_strict);
  return UNITY_END();
}
