#include <unity.h>

#include "../support/serial_replay.h"

void test_replay_preserves_exact_bytes() {
  SerialReplay<32> replay;
  replay.write(" ");
  replay.write("ok\n");

  TEST_ASSERT_EQUAL_STRING(" ok\n", replay.bytes());
  TEST_ASSERT_FALSE(replay.overflowed());
}

void test_replay_reports_overflow_without_partial_write() {
  SerialReplay<4> replay;
  replay.write("ok\n");
  replay.write("xx");

  TEST_ASSERT_EQUAL_STRING("ok\n", replay.bytes());
  TEST_ASSERT_TRUE(replay.overflowed());
}

void test_replay_can_be_reused_after_clear() {
  SerialReplay<8> replay;
  replay.write("stopped\n");
  replay.clear();
  replay.write("done\n");

  TEST_ASSERT_EQUAL_STRING("done\n", replay.bytes());
  TEST_ASSERT_FALSE(replay.overflowed());
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_replay_preserves_exact_bytes);
  RUN_TEST(test_replay_reports_overflow_without_partial_write);
  RUN_TEST(test_replay_can_be_reused_after_clear);
  return UNITY_END();
}
