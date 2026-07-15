#include <unity.h>

#include "logic.h"

void test_speed_conversion_rejects_out_of_range_values() {
  TEST_ASSERT_EQUAL_INT(500, convertSpeedToDelay(0));
  TEST_ASSERT_EQUAL_INT(500, convertSpeedToDelay(101));
}

void test_speed_conversion_preserves_known_values() {
  TEST_ASSERT_EQUAL_INT(1000, convertSpeedToDelay(1));
  TEST_ASSERT_EQUAL_INT(535, convertSpeedToDelay(50));
  TEST_ASSERT_EQUAL_INT(60, convertSpeedToDelay(100));
}

void test_clamp_byte_limits_values() {
  TEST_ASSERT_EQUAL_INT(0, clampByte(-10));
  TEST_ASSERT_EQUAL_INT(0, clampByte(0));
  TEST_ASSERT_EQUAL_INT(128, clampByte(128));
  TEST_ASSERT_EQUAL_INT(255, clampByte(255));
  TEST_ASSERT_EQUAL_INT(255, clampByte(300));
  TEST_ASSERT_EQUAL_INT(0, clampByte(INT32_MIN));
  TEST_ASSERT_EQUAL_INT(255, clampByte(INT32_MAX));
}

void test_parse_uint32_accepts_complete_in_range_values() {
  uint32_t value = 99;

  TEST_ASSERT_TRUE(parseUint32("0", 100, &value));
  TEST_ASSERT_EQUAL_UINT32(0, value);
  TEST_ASSERT_TRUE(parseUint32("100", 100, &value));
  TEST_ASSERT_EQUAL_UINT32(100, value);
}

void test_parse_uint32_rejects_invalid_or_overflowing_values() {
  uint32_t value = 42;

  TEST_ASSERT_FALSE(parseUint32("", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("-1", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("60.5", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("60seconds", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("101", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("1", 0, &value));
  TEST_ASSERT_EQUAL_UINT32(42, value);
}

void test_parse_int32_accepts_signed_bounds() {
  int32_t value = 99;

  TEST_ASSERT_TRUE(parseInt32("-32768", -32768, 32767, &value));
  TEST_ASSERT_EQUAL_INT32(-32768, value);
  TEST_ASSERT_TRUE(parseInt32("+255", -32768, 32767, &value));
  TEST_ASSERT_EQUAL_INT32(255, value);
  TEST_ASSERT_TRUE(parseInt32("32767", -32768, 32767, &value));
  TEST_ASSERT_EQUAL_INT32(32767, value);
  TEST_ASSERT_TRUE(parseInt32("-2147483648", INT32_MIN, INT32_MAX, &value));
  TEST_ASSERT_EQUAL_INT32(INT32_MIN, value);
  TEST_ASSERT_TRUE(parseInt32("2147483647", INT32_MIN, INT32_MAX, &value));
  TEST_ASSERT_EQUAL_INT32(INT32_MAX, value);
}

void test_parse_int32_rejects_invalid_or_out_of_range_values() {
  int32_t value = 42;

  TEST_ASSERT_FALSE(parseInt32("", -32768, 32767, &value));
  TEST_ASSERT_FALSE(parseInt32("-", -32768, 32767, &value));
  TEST_ASSERT_FALSE(parseInt32("-32769", -32768, 32767, &value));
  TEST_ASSERT_FALSE(parseInt32("32768", -32768, 32767, &value));
  TEST_ASSERT_FALSE(
      parseInt32("-2147483649", INT32_MIN, INT32_MAX, &value));
  TEST_ASSERT_FALSE(parseInt32("2147483648", INT32_MIN, INT32_MAX, &value));
  TEST_ASSERT_FALSE(parseInt32("1.5", -32768, 32767, &value));
  TEST_ASSERT_FALSE(parseInt32("12x", -32768, 32767, &value));
  TEST_ASSERT_EQUAL_INT32(42, value);
}

void test_parse_bool_accepts_legacy_values() {
  bool value = false;

  TEST_ASSERT_TRUE(parseBool("true", &value));
  TEST_ASSERT_TRUE(value);
  TEST_ASSERT_TRUE(parseBool("TRUE", &value));
  TEST_ASSERT_TRUE(value);
  TEST_ASSERT_TRUE(parseBool("1", &value));
  TEST_ASSERT_TRUE(value);
  TEST_ASSERT_TRUE(parseBool("false", &value));
  TEST_ASSERT_FALSE(value);
  TEST_ASSERT_TRUE(parseBool("FALSE", &value));
  TEST_ASSERT_FALSE(value);
  TEST_ASSERT_TRUE(parseBool("0", &value));
  TEST_ASSERT_FALSE(value);
}

void test_parse_bool_rejects_malformed_values_without_update() {
  bool value = true;

  TEST_ASSERT_FALSE(parseBool("", &value));
  TEST_ASSERT_FALSE(parseBool("yes", &value));
  TEST_ASSERT_FALSE(parseBool("10", &value));
  TEST_ASSERT_TRUE(value);
}

void test_standby_timeout_conversion_checks_overflow() {
  uint32_t milliseconds = 0;

  TEST_ASSERT_TRUE(secondsToMilliseconds(60, &milliseconds));
  TEST_ASSERT_EQUAL_UINT32(60000, milliseconds);
  TEST_ASSERT_TRUE(
      secondsToMilliseconds(MAX_STANDBY_TIMEOUT_SECONDS, &milliseconds));
  TEST_ASSERT_FALSE(
      secondsToMilliseconds(MAX_STANDBY_TIMEOUT_SECONDS + 1, &milliseconds));
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_speed_conversion_rejects_out_of_range_values);
  RUN_TEST(test_speed_conversion_preserves_known_values);
  RUN_TEST(test_clamp_byte_limits_values);
  RUN_TEST(test_parse_uint32_accepts_complete_in_range_values);
  RUN_TEST(test_parse_uint32_rejects_invalid_or_overflowing_values);
  RUN_TEST(test_parse_int32_accepts_signed_bounds);
  RUN_TEST(test_parse_int32_rejects_invalid_or_out_of_range_values);
  RUN_TEST(test_parse_bool_accepts_legacy_values);
  RUN_TEST(test_parse_bool_rejects_malformed_values_without_update);
  RUN_TEST(test_standby_timeout_conversion_checks_overflow);
  return UNITY_END();
}
