#include <string.h>
#include <unity.h>

#include "command_parser.h"

void test_partial_input_waits_for_newline() {
  CommandParser parser;

  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('p'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('i'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('n'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('g'));
  TEST_ASSERT_EQUAL_STRING("ping", parser.frame());
  TEST_ASSERT_EQUAL(CommandParser::FrameReady, parser.consume('\n'));
  TEST_ASSERT_EQUAL_STRING("ping", parser.frame());
}

void test_multiple_frames_in_one_burst() {
  CommandParser parser;
  const char input[] = "ping\nversion\n";
  const char *expected[] = {"ping", "version"};
  size_t frameIndex = 0;

  for (size_t index = 0; index < sizeof(input) - 1; ++index) {
    if (parser.consume(input[index]) == CommandParser::FrameReady) {
      TEST_ASSERT_TRUE(frameIndex < 2);
      TEST_ASSERT_EQUAL_STRING(expected[frameIndex++], parser.frame());
      parser.reset();
    }
  }
  TEST_ASSERT_EQUAL_UINT32(2, frameIndex);
}

void test_crlf_drops_one_carriage_return() {
  CommandParser parser;

  const char input[] = "ping\r\n";
  for (size_t index = 0; index < sizeof(input) - 2; ++index) {
    TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume(input[index]));
  }
  TEST_ASSERT_EQUAL(CommandParser::FrameReady,
                    parser.consume(input[sizeof(input) - 2]));
  TEST_ASSERT_EQUAL_STRING("ping", parser.frame());
}

void test_exact_capacity_is_accepted_with_crlf() {
  CommandParser parser;

  for (size_t index = 0; index < COMMAND_MAX_LENGTH; ++index) {
    TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('x'));
  }
  TEST_ASSERT_EQUAL_UINT32(COMMAND_MAX_LENGTH, parser.length());
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('\r'));
  TEST_ASSERT_EQUAL(CommandParser::FrameReady, parser.consume('\n'));
  TEST_ASSERT_EQUAL_UINT32(COMMAND_MAX_LENGTH, strlen(parser.frame()));
}

void test_one_byte_overflow_discards_entire_frame() {
  CommandParser parser;

  for (size_t index = 0; index <= COMMAND_MAX_LENGTH; ++index) {
    TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('x'));
  }
  TEST_ASSERT_EQUAL(CommandParser::FrameOverflow, parser.consume('\n'));
}

void test_long_overflow_reports_once_at_newline() {
  CommandParser parser;

  for (size_t index = 0; index < COMMAND_MAX_LENGTH + 100; ++index) {
    TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('x'));
  }
  TEST_ASSERT_EQUAL_UINT32(COMMAND_MAX_LENGTH, parser.length());
  TEST_ASSERT_EQUAL(CommandParser::FrameOverflow, parser.consume('\n'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('p'));
}

void test_parser_recovers_after_overflow() {
  CommandParser parser;

  for (size_t index = 0; index <= COMMAND_MAX_LENGTH; ++index) {
    parser.consume('x');
  }
  TEST_ASSERT_EQUAL(CommandParser::FrameOverflow, parser.consume('\n'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('p'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('i'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('n'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('g'));
  TEST_ASSERT_EQUAL(CommandParser::FrameReady, parser.consume('\n'));
  TEST_ASSERT_EQUAL_STRING("ping", parser.frame());
}

void test_empty_frame_is_safe_and_deterministic() {
  CommandParser parser;

  TEST_ASSERT_EQUAL(CommandParser::FrameReady, parser.consume('\n'));
  TEST_ASSERT_EQUAL_UINT32(0, parser.length());
  TEST_ASSERT_EQUAL_STRING("", parser.frame());
}

void test_embedded_null_discards_frame_and_recovers() {
  CommandParser parser;
  const char invalid[] = {'p', 'i', '\0', 'n', 'g', '\n'};

  for (size_t index = 0; index < sizeof(invalid) - 1; ++index) {
    TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume(invalid[index]));
  }
  TEST_ASSERT_EQUAL(CommandParser::FrameInvalid,
                    parser.consume(invalid[sizeof(invalid) - 1]));

  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('p'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('i'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('n'));
  TEST_ASSERT_EQUAL(CommandParser::NoFrame, parser.consume('g'));
  TEST_ASSERT_EQUAL(CommandParser::FrameReady, parser.consume('\n'));
  TEST_ASSERT_EQUAL_STRING("ping", parser.frame());
}

void test_pending_command_holds_exactly_one_complete_frame() {
  PendingCommand pending;

  TEST_ASSERT_TRUE(pending.enqueue("version", 7));
  TEST_ASSERT_TRUE(pending.available());
  TEST_ASSERT_EQUAL_STRING("version", pending.frame());

  TEST_ASSERT_FALSE(pending.enqueue("ping", 4));
  TEST_ASSERT_EQUAL_STRING("version", pending.frame());
}

void test_pending_command_clear_allows_a_fresh_frame() {
  PendingCommand pending;

  TEST_ASSERT_TRUE(pending.enqueue("version", 7));
  pending.clear();
  TEST_ASSERT_FALSE(pending.available());
  TEST_ASSERT_EQUAL_STRING("", pending.frame());

  TEST_ASSERT_TRUE(pending.enqueue("ping", 4));
  TEST_ASSERT_EQUAL_STRING("ping", pending.frame());
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_partial_input_waits_for_newline);
  RUN_TEST(test_multiple_frames_in_one_burst);
  RUN_TEST(test_crlf_drops_one_carriage_return);
  RUN_TEST(test_exact_capacity_is_accepted_with_crlf);
  RUN_TEST(test_one_byte_overflow_discards_entire_frame);
  RUN_TEST(test_long_overflow_reports_once_at_newline);
  RUN_TEST(test_parser_recovers_after_overflow);
  RUN_TEST(test_empty_frame_is_safe_and_deterministic);
  RUN_TEST(test_embedded_null_discards_frame_and_recovers);
  RUN_TEST(test_pending_command_holds_exactly_one_complete_frame);
  RUN_TEST(test_pending_command_clear_allows_a_fresh_frame);
  return UNITY_END();
}
