#include <string.h>

#include <unity.h>

#include "command_parser.h"
#include "protocol.h"

#if PROTOCOL_V2_ENABLED

static V2NegotiationAction negotiate(const char *command, bool busy = false,
                                     bool pending = false) {
  return dispatchV2Negotiation(command, strlen(command), busy, pending);
}

static V2Protocol1Action returnToV1(const char *command, bool busy,
                                    bool pending, uint16_t *requestId) {
  return dispatchV2Protocol1(command, strlen(command), busy, pending,
                             requestId);
}

void test_discovery_and_activation_are_exact_and_idle_only() {
  TEST_ASSERT_EQUAL_STRING("protocol:2 available\n", v2DiscoveryResponse());
  TEST_ASSERT_EQUAL_STRING("protocol:2 ready\n", v2ActivationResponse());
  TEST_ASSERT_EQUAL(static_cast<int>(V2NegotiationAction::Discovery),
                    static_cast<int>(negotiate("protocol:2?")));
  TEST_ASSERT_EQUAL(static_cast<int>(V2NegotiationAction::Activate),
                    static_cast<int>(negotiate("protocol:2")));
  TEST_ASSERT_EQUAL(static_cast<int>(V2NegotiationAction::Busy),
                    static_cast<int>(negotiate("protocol:2?", true)));
  TEST_ASSERT_EQUAL(static_cast<int>(V2NegotiationAction::Busy),
                    static_cast<int>(negotiate("protocol:2", false, true)));
  TEST_ASSERT_EQUAL_STRING("error:busy\n", v1ResponseText(V1Response::Busy));
}

void test_negotiation_does_not_match_windows_traffic_variants() {
  const char *variants[] = {
      " protocol:2?", "protocol:2? ", "Protocol:2?", "protocol:2?\r",
      "protocol:2\n",  "protocol:02?", "protocol:2 ?",
      " protocol:2",  "protocol:2 ",  "Protocol:2",
      "protocol:02",  "protocol:2\r"};
  for (size_t index = 0; index < sizeof(variants) / sizeof(variants[0]);
       ++index) {
    TEST_ASSERT_EQUAL(static_cast<int>(V2NegotiationAction::NotHandled),
                      static_cast<int>(negotiate(variants[index])));
  }
}

void test_activation_and_reset_create_clean_session_boundaries() {
  ProtocolSession session;
  TEST_ASSERT_EQUAL(static_cast<int>(ProtocolMode::V1),
                    static_cast<int>(session.mode()));
  TEST_ASSERT_EQUAL(0, session.activeRequestId());
  TEST_ASSERT_EQUAL(0, session.eventSequence());
  TEST_ASSERT_FALSE(session.crcEnabled());

  session.enterV2();
  TEST_ASSERT_EQUAL(static_cast<int>(ProtocolMode::V2),
                    static_cast<int>(session.mode()));
  TEST_ASSERT_EQUAL(0, session.activeRequestId());
  TEST_ASSERT_EQUAL(1, session.eventSequence());
  TEST_ASSERT_FALSE(session.crcEnabled());

  session.reset();
  TEST_ASSERT_EQUAL(static_cast<int>(ProtocolMode::V1),
                    static_cast<int>(session.mode()));
  TEST_ASSERT_EQUAL(0, session.activeRequestId());
  TEST_ASSERT_EQUAL(0, session.eventSequence());
  TEST_ASSERT_FALSE(session.crcEnabled());
}

void test_session_boundaries_clear_legacy_parser_and_pending_state() {
  CommandParser parser;
  PendingCommand pending;
  TEST_ASSERT_EQUAL(static_cast<int>(CommandParser::NoFrame),
                    static_cast<int>(parser.consume('p')));
  TEST_ASSERT_TRUE(pending.enqueue("legacy", 6));

  parser.reset();
  pending.clear();
  ProtocolSession session;
  session.enterV2();
  TEST_ASSERT_EQUAL_STRING("", parser.frame());
  TEST_ASSERT_EQUAL(0, parser.length());
  TEST_ASSERT_FALSE(pending.available());
  TEST_ASSERT_EQUAL(static_cast<int>(ProtocolMode::V2),
                    static_cast<int>(session.mode()));

  TEST_ASSERT_EQUAL(static_cast<int>(CommandParser::NoFrame),
                    static_cast<int>(parser.consume('@')));
  TEST_ASSERT_TRUE(pending.enqueue("legacy", 6));
  parser.reset();
  pending.clear();
  session.reset();
  TEST_ASSERT_EQUAL_STRING("", parser.frame());
  TEST_ASSERT_EQUAL(0, parser.length());
  TEST_ASSERT_FALSE(pending.available());
  TEST_ASSERT_EQUAL(static_cast<int>(ProtocolMode::V1),
                    static_cast<int>(session.mode()));
}

void test_protocol_one_accepts_idless_and_explicit_ids() {
  uint16_t requestId = 99;
  TEST_ASSERT_EQUAL(static_cast<int>(V2Protocol1Action::ReturnToV1),
                    static_cast<int>(
                        returnToV1("protocol:1", false, false, &requestId)));
  TEST_ASSERT_EQUAL(0, requestId);

  TEST_ASSERT_EQUAL(static_cast<int>(V2Protocol1Action::ReturnToV1),
                    static_cast<int>(
                        returnToV1("@65535 protocol:1", false, false,
                                   &requestId)));
  TEST_ASSERT_EQUAL(65535, requestId);

  char response[32];
  formatV2Protocol1Response(response, sizeof(response), 0, false);
  TEST_ASSERT_EQUAL_STRING("@0 done:protocol=1\n", response);
  formatV2Protocol1Response(response, sizeof(response), 65535, false);
  TEST_ASSERT_EQUAL_STRING("@65535 done:protocol=1\n", response);
}

void test_protocol_one_busy_remains_v2_and_is_correlated() {
  uint16_t requestId = 0;
  TEST_ASSERT_EQUAL(static_cast<int>(V2Protocol1Action::Busy),
                    static_cast<int>(
                        returnToV1("@12 protocol:1", true, false, &requestId)));
  TEST_ASSERT_EQUAL(12, requestId);
  char response[32];
  formatV2Protocol1Response(response, sizeof(response), requestId, true);
  TEST_ASSERT_EQUAL_STRING("@12 error:2001:busy\n", response);

  TEST_ASSERT_EQUAL(static_cast<int>(V2Protocol1Action::Busy),
                    static_cast<int>(
                        returnToV1("protocol:1", false, true, &requestId)));
  TEST_ASSERT_EQUAL(0, requestId);
  formatV2Protocol1Response(response, sizeof(response), requestId, true);
  TEST_ASSERT_EQUAL_STRING("@0 error:2001:busy\n", response);
}

void test_protocol_one_rejects_invalid_envelopes_without_mode_change() {
  const char *invalid[] = {
      "@0 protocol:1", "@ protocol:1", "@1  protocol:1", "@1 protocol:1 ",
      " @1 protocol:1", "@65536 protocol:1", "@1 protocol:01"};
  uint16_t requestId = 0;
  for (size_t index = 0; index < sizeof(invalid) / sizeof(invalid[0]);
       ++index) {
    TEST_ASSERT_EQUAL(static_cast<int>(V2Protocol1Action::NotHandled),
                      static_cast<int>(
                          returnToV1(invalid[index], false, false, &requestId)));
  }
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_discovery_and_activation_are_exact_and_idle_only);
  RUN_TEST(test_negotiation_does_not_match_windows_traffic_variants);
  RUN_TEST(test_activation_and_reset_create_clean_session_boundaries);
  RUN_TEST(test_session_boundaries_clear_legacy_parser_and_pending_state);
  RUN_TEST(test_protocol_one_accepts_idless_and_explicit_ids);
  RUN_TEST(test_protocol_one_busy_remains_v2_and_is_correlated);
  RUN_TEST(test_protocol_one_rejects_invalid_envelopes_without_mode_change);
  return UNITY_END();
}

#else

void test_v2_session_code_is_compiled_out_by_default() {
  ProtocolSession session;
  TEST_ASSERT_EQUAL(static_cast<int>(ProtocolMode::V1),
                    static_cast<int>(session.mode()));
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_v2_session_code_is_compiled_out_by_default);
  return UNITY_END();
}

#endif
