#include <string.h>

#include <unity.h>

#include "protocol.h"
#include "v2_parser.h"

#if PROTOCOL_V2_ENABLED

static void assertEnvelope(const char *frame, uint16_t requestId,
                           const char *payload, bool explicitId) {
  V2RequestEnvelope envelope;
  TEST_ASSERT_EQUAL(static_cast<int>(V2EnvelopeStatus::Ready),
                    static_cast<int>(parseV2RequestEnvelope(
                        frame, strlen(frame), &envelope)));
  TEST_ASSERT_EQUAL(requestId, envelope.requestId);
  TEST_ASSERT_EQUAL_STRING(payload, envelope.payload);
  TEST_ASSERT_EQUAL(explicitId, envelope.explicitId);
}

void test_v2_parser_accepts_64_byte_printable_crlf_frames() {
  V2FrameParser parser;
  const char *first = "ping\r\n";
  for (size_t index = 0; index < strlen(first); ++index) {
    const V2FrameParser::Result result = parser.consume(first[index]);
    if (index + 1 == strlen(first)) {
      TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::FrameReady),
                        static_cast<int>(result));
    } else {
      TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::NoFrame),
                        static_cast<int>(result));
    }
  }
  TEST_ASSERT_EQUAL_STRING("ping", parser.frame());
  TEST_ASSERT_EQUAL(4, parser.length());
  parser.reset();

  char longest[V2_MAX_LINE_LENGTH + 1];
  memset(longest, 'a', V2_MAX_LINE_LENGTH);
  longest[V2_MAX_LINE_LENGTH] = '\0';
  for (size_t index = 0; index < V2_MAX_LINE_LENGTH; ++index) {
    TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::NoFrame),
                      static_cast<int>(parser.consume(longest[index])));
  }
  TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::FrameReady),
                    static_cast<int>(parser.consume('\n')));
  TEST_ASSERT_EQUAL(V2_MAX_LINE_LENGTH, parser.length());
}

void test_v2_parser_handles_partial_concatenated_and_bad_frames() {
  V2FrameParser parser;
  const char *second = "@7 version\n";
  for (size_t index = 0; index < strlen(second); ++index) {
    const V2FrameParser::Result result = parser.consume(second[index]);
    if (index + 1 == strlen(second)) {
      TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::FrameReady),
                        static_cast<int>(result));
    }
  }
  TEST_ASSERT_EQUAL_STRING("@7 version", parser.frame());
  parser.reset();

  TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::NoFrame),
                    static_cast<int>(parser.consume('p')));
  TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::NoFrame),
                    static_cast<int>(parser.consume('\0')));
  TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::FrameInvalid),
                    static_cast<int>(parser.consume('\n')));

  parser.reset();
  for (size_t index = 0; index <= V2_MAX_LINE_LENGTH; ++index) {
    parser.consume('a');
  }
  TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::FrameOverflow),
                    static_cast<int>(parser.consume('\n')));
}

void test_v2_envelope_enforces_ids_and_whitespace() {
  assertEnvelope("ping", 0, "ping", false);
  assertEnvelope("@1 ping", 1, "ping", true);
  assertEnvelope("@65535 version", 65535, "version", true);

  const char *badIds[] = {"@0 ping", "@ ping", "@65536 ping", "@1ping"};
  for (size_t index = 0; index < sizeof(badIds) / sizeof(badIds[0]); ++index) {
    V2RequestEnvelope envelope;
    TEST_ASSERT_EQUAL(static_cast<int>(V2EnvelopeStatus::BadId),
                      static_cast<int>(parseV2RequestEnvelope(
                          badIds[index], strlen(badIds[index]), &envelope)));
  }

  const char *badFrames[] = {"", " ping", "ping ", "@1  ping", "@1 ping "};
  for (size_t index = 0; index < sizeof(badFrames) / sizeof(badFrames[0]);
       ++index) {
    V2RequestEnvelope envelope;
    TEST_ASSERT_EQUAL(static_cast<int>(V2EnvelopeStatus::BadFrame),
                      static_cast<int>(parseV2RequestEnvelope(
                          badFrames[index], strlen(badFrames[index]), &envelope)));
  }
}

void test_v2_lifecycle_allows_one_active_and_one_immediate_read_only() {
  V2RequestLifecycle lifecycle;
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginActive(42)));
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginReadOnly(7)));
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::DuplicateId),
                    static_cast<int>(lifecycle.beginReadOnly(42)));
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Busy),
                    static_cast<int>(lifecycle.beginActive(43)));
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Busy),
                    static_cast<int>(lifecycle.beginReadOnly(8)));
  TEST_ASSERT_TRUE(lifecycle.terminal(7));
  TEST_ASSERT_TRUE(lifecycle.terminal(42));
  TEST_ASSERT_FALSE(lifecycle.terminal(42));

  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginReadOnly(9)));
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginActive(10)));
  lifecycle.reset();
  TEST_ASSERT_FALSE(lifecycle.isActive());
  TEST_ASSERT_FALSE(lifecycle.owns(9));

  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginActive(0)));
  TEST_ASSERT_TRUE(lifecycle.isIdlessActive());
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Busy),
                    static_cast<int>(lifecycle.beginReadOnly(0)));
}

struct V2Capture {
  char line[V2_MAX_LINE_LENGTH + 2];
  size_t length;
  unsigned int writes;
};

static bool captureV2Line(void *context, const char *line, size_t length) {
  V2Capture *capture = static_cast<V2Capture *>(context);
  if (length >= sizeof(capture->line)) return false;
  memcpy(capture->line, line, length);
  capture->line[length] = '\0';
  capture->length = length;
  ++capture->writes;
  return true;
}

void test_v2_formatters_are_bounded_and_emit_one_lf() {
  char line[V2_MAX_LINE_LENGTH + 2];
  TEST_ASSERT_TRUE(formatV2Response(line, sizeof(line), 12,
                                    V2ResponseKind::Done, "uptime_ms=1"));
  TEST_ASSERT_EQUAL_STRING("@12 done:uptime_ms=1\n", line);
  TEST_ASSERT_EQUAL('\n', line[strlen(line) - 1]);
  TEST_ASSERT_NOT_EQUAL('\n', line[strlen(line) - 2]);

  char detail[58];
  memset(detail, 'a', sizeof(detail) - 1);
  detail[sizeof(detail) - 1] = '\0';
  TEST_ASSERT_TRUE(formatV2Event(line, sizeof(line), 65535, detail));
  TEST_ASSERT_EQUAL(V2_MAX_LINE_LENGTH + 1, strlen(line));

  char tooSmall[V2_MAX_LINE_LENGTH + 1];
  memset(tooSmall, 'x', sizeof(tooSmall));
  TEST_ASSERT_FALSE(formatV2Event(tooSmall, sizeof(tooSmall), 65535, detail));
  TEST_ASSERT_EQUAL('\0', tooSmall[0]);
}

void test_v2_terminal_is_emitted_once_and_events_wrap() {
  V2RequestLifecycle lifecycle;
  V2Capture capture = {};
  V2OutputWriter writer = {&capture, captureV2Line};
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginActive(15)));
  TEST_ASSERT_TRUE(emitV2Terminal(&lifecycle, writer, 15,
                                  V2ResponseKind::Done, "slot=3"));
  TEST_ASSERT_EQUAL_STRING("@15 done:slot=3\n", capture.line);
  TEST_ASSERT_FALSE(emitV2Terminal(&lifecycle, writer, 15,
                                   V2ResponseKind::Done, "slot=3"));
  TEST_ASSERT_EQUAL(1, capture.writes);

  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginReadOnly(16)));
  TEST_ASSERT_TRUE(emitV2Terminal(&lifecycle, writer, 16,
                                  V2ResponseKind::Error,
                                  "1004:unknown_command"));
  TEST_ASSERT_EQUAL_STRING("@16 error:1004:unknown_command\n", capture.line);

  ProtocolSession session;
  session.enterV2();
  for (uint32_t index = 1; index < 65535U; ++index) {
    session.nextEventSequence();
  }
  TEST_ASSERT_EQUAL(65535, session.eventSequence());
  TEST_ASSERT_EQUAL(65535, session.nextEventSequence());
  TEST_ASSERT_EQUAL(1, session.nextEventSequence());
  TEST_ASSERT_EQUAL(2, session.eventSequence());
}

void test_v2_session_reset_clears_parser_lifecycle_and_sequence() {
  ProtocolSession session;
  session.enterV2();
  TEST_ASSERT_EQUAL(static_cast<int>(V2FrameParser::NoFrame),
                    static_cast<int>(session.parser().consume('@')));
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(session.lifecycle().beginActive(42)));
  TEST_ASSERT_EQUAL(1, session.nextEventSequence());

  session.reset();
  TEST_ASSERT_EQUAL(static_cast<int>(ProtocolMode::V1),
                    static_cast<int>(session.mode()));
  TEST_ASSERT_EQUAL_STRING("", session.parser().frame());
  TEST_ASSERT_EQUAL(0, session.parser().length());
  TEST_ASSERT_FALSE(session.lifecycle().owns(42));
  TEST_ASSERT_EQUAL(0, session.eventSequence());
  TEST_ASSERT_FALSE(session.crcEnabled());
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_v2_parser_accepts_64_byte_printable_crlf_frames);
  RUN_TEST(test_v2_parser_handles_partial_concatenated_and_bad_frames);
  RUN_TEST(test_v2_envelope_enforces_ids_and_whitespace);
  RUN_TEST(test_v2_lifecycle_allows_one_active_and_one_immediate_read_only);
  RUN_TEST(test_v2_formatters_are_bounded_and_emit_one_lf);
  RUN_TEST(test_v2_terminal_is_emitted_once_and_events_wrap);
  RUN_TEST(test_v2_session_reset_clears_parser_lifecycle_and_sequence);
  return UNITY_END();
}

#else

int main(int, char **) {
  UNITY_BEGIN();
  return UNITY_END();
}

#endif
