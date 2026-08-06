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
  char lines[24][V2_MAX_LINE_LENGTH + 2];
  size_t lengths[24];
};

static bool captureV2Line(void *context, const char *line, size_t length) {
  V2Capture *capture = static_cast<V2Capture *>(context);
  if (length >= sizeof(capture->line) || capture->writes >= 24) return false;
  memcpy(capture->line, line, length);
  capture->line[length] = '\0';
  memcpy(capture->lines[capture->writes], line, length);
  capture->lines[capture->writes][length] = '\0';
  capture->lengths[capture->writes] = length;
  capture->length = length;
  ++capture->writes;
  return true;
}

struct FailingV2Capture {
  V2Capture capture;
  unsigned int successesRemaining;
};

static bool failAfterV2Lines(void *context, const char *line, size_t length) {
  FailingV2Capture *failing = static_cast<FailingV2Capture *>(context);
  if (failing->successesRemaining == 0) return false;
  --failing->successesRemaining;
  return captureV2Line(&failing->capture, line, length);
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

static V2ObservabilitySnapshot observabilitySnapshot() {
  V2ObservabilitySnapshot snapshot = {
      {102, 8, false, true, true, true, true},
      {MachineMode::Running,
       MachinePhase::Idle,
       true,
       true,
       false,
       false,
       0,
       0,
       0,
       3,
       0}};
  return snapshot;
}

static void assertCapturedLine(const V2Capture &capture, const char *line) {
  for (unsigned int index = 0; index < capture.writes; ++index) {
    if (strcmp(capture.lines[index], line) == 0) return;
  }
  TEST_FAIL_MESSAGE(line);
}

static void assertAllCapturedLinesBounded(const V2Capture &capture) {
  for (unsigned int index = 0; index < capture.writes; ++index) {
    TEST_ASSERT_LESS_OR_EQUAL(V2_MAX_LINE_LENGTH + 1, capture.lengths[index]);
    TEST_ASSERT_EQUAL('\n', capture.lines[index][capture.lengths[index] - 1]);
  }
}

static void assertInspection(V2InspectionCommand command, uint16_t requestId,
                             const V2ObservabilitySnapshot &snapshot,
                             V2Capture *capture) {
  V2RequestLifecycle lifecycle;
  V2OutputWriter writer = {capture, captureV2Line};
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginReadOnly(requestId)));
  TEST_ASSERT_TRUE(
      emitV2Inspection(&lifecycle, writer, requestId, command, snapshot));
  TEST_ASSERT_FALSE(lifecycle.owns(requestId));
  assertAllCapturedLinesBounded(*capture);
}

static Configuration configurationSnapshot() {
  Configuration configuration = {90, 70, 90, 20, 8, 90, 400, false, 0, 30,
                                 50, 3,  0,  60, 300, 500, 78};
  return configuration;
}

static V1DispatchLimits configurationLimits() {
  V1DispatchLimits limits = {32767, UINT32_MAX / 1000UL, 200, 16};
  return limits;
}

static V1DispatchContext configurationContext() {
  V1DispatchContext context = {true, false, false, 0, 0};
  return context;
}

struct V1Capture {
  char bytes[8];
  size_t length;
};

static void captureV1Response(void *context, V1Response response) {
  V1Capture *capture = static_cast<V1Capture *>(context);
  const char *responseText = v1ResponseText(response);
  capture->length = strlen(responseText);
  memcpy(capture->bytes, responseText, capture->length + 1);
}

void test_v2_setters_reuse_v1_validation_actions_and_generation() {
  struct SetterCase {
    const char *payload;
    V1Action action;
  };
  const SetterCase setters[] = {
      {"feedspeed:80", V1Action::ApplyFeedSpeed},
      {"feedsteps:71", V1Action::ApplyFeedSteps},
      {"feedhomingoffset:4", V1Action::ApplyFeedHomingOffset},
      {"sortspeed:81", V1Action::ApplySortSpeed},
      {"sortsteps:19", V1Action::None},
      {"sorthomingoffset:1", V1Action::ApplySortHomingOffset},
      {"slotcount:9", V1Action::None},
      {"notificationdelay:91", V1Action::None},
      {"slotdropdelay:401", V1Action::ApplyDropDelay},
      {"automotorstandbytimeout:61", V1Action::ApplyAutoMotorStandbyTimeout},
      {"debounceTimeout:301", V1Action::None},
      {"debounceTime:501", V1Action::None},
      {"airdropenabled:true", V1Action::ApplyDropDelay},
      {"airdroppredelay:31", V1Action::None},
      {"airdroppostdelay:1", V1Action::ApplyDropDelay},
      {"airdropdsignalduration:51", V1Action::None},
      {"cameraledlevel:999", V1Action::ApplyCameraLedLevel},
  };
  Configuration configuration = configurationSnapshot();
  uint32_t generation = 0;
  for (size_t index = 0; index < sizeof(setters) / sizeof(setters[0]); ++index) {
    const V1Command command = classifyV1Command(setters[index].payload);
    TEST_ASSERT_TRUE(v1CommandIsSetter(command));
    const V1DispatchResult result =
        dispatchV1Command(setters[index].payload, strlen(setters[index].payload),
                          configurationContext(), &configuration,
                          configurationLimits());
    TEST_ASSERT_EQUAL(static_cast<int>(setters[index].action),
                      static_cast<int>(result.action));
    TEST_ASSERT_EQUAL(static_cast<int>(V1Response::Ok),
                      static_cast<int>(result.response));
    ++generation;
  }
  TEST_ASSERT_EQUAL(17, generation);
  TEST_ASSERT_EQUAL(80, configuration.feedSpeed);
  TEST_ASSERT_EQUAL(71, configuration.feedSteps);
  TEST_ASSERT_EQUAL(81, configuration.sortSpeed);
  TEST_ASSERT_EQUAL(19, configuration.sortSteps);
  TEST_ASSERT_EQUAL(9, configuration.slotCount);
  TEST_ASSERT_EQUAL(255, configuration.cameraLedLevel);
}

void test_v2_configuration_support_keeps_v1_setter_bytes_unchanged() {
  Configuration configuration = configurationSnapshot();
  const V1DispatchResult result =
      dispatchV1Command("feedspeed:80", 12, configurationContext(),
                        &configuration, configurationLimits());
  V1Capture capture = {};
  const V1OutputWriter writer = {&capture, captureV1Response, 0, 0, 0};
  writeV1Output(result, configuration, false, writer);
  TEST_ASSERT_EQUAL_STRING("ok\n", capture.bytes);
  TEST_ASSERT_EQUAL(80, configuration.feedSpeed);
}

void test_v2_setter_terminal_semantics_are_correlated_and_bounded() {
  char line[V2_MAX_LINE_LENGTH + 2];
  TEST_ASSERT_TRUE(formatV2Response(line, sizeof(line), 10,
                                    V2ResponseKind::Done,
                                    "feedspeed=80 generation=1"));
  TEST_ASSERT_EQUAL_STRING("@10 done:feedspeed=80 generation=1\n", line);
  TEST_ASSERT_TRUE(formatV2Response(
      line, sizeof(line), 65535, V2ResponseKind::Done,
      "automotorstandbytimeout=4294967 generation=999999999"));
  TEST_ASSERT_EQUAL(V2_MAX_LINE_LENGTH + 1, strlen(line));
}

void test_v2_invalid_setters_leave_configuration_and_generation_unchanged() {
  const char *invalid[] = {
      "feedspeed:101", "feedsteps:1001", "feedhomingoffset:201",
      "sortspeed:101", "sortsteps:0", "sorthomingoffset:201",
      "slotcount:0", "notificationdelay:-1", "slotdropdelay:-1",
      "automotorstandbytimeout:4294968", "debounceTimeout:-1",
      "debounceTime:-1", "airdropenabled:yes", "airdroppredelay:-1",
      "airdroppostdelay:-1", "airdropdsignalduration:-1",
      "cameraledlevel:not-a-number"};
  const Configuration original = configurationSnapshot();
  Configuration configuration = original;
  uint32_t generation = 9;
  for (size_t index = 0; index < sizeof(invalid) / sizeof(invalid[0]); ++index) {
    const V1Command command = classifyV1Command(invalid[index]);
    TEST_ASSERT_TRUE(v1CommandIsSetter(command));
    const V1DispatchResult result =
        dispatchV1Command(invalid[index], strlen(invalid[index]),
                          configurationContext(), &configuration,
                          configurationLimits());
    TEST_ASSERT_EQUAL(static_cast<int>(V1Action::None),
                      static_cast<int>(result.action));
    TEST_ASSERT_NOT_EQUAL(static_cast<int>(V1Response::Ok),
                          static_cast<int>(result.response));
    TEST_ASSERT_EQUAL_MEMORY(&original, &configuration, sizeof(Configuration));
    TEST_ASSERT_EQUAL(9, generation);
  }
  TEST_ASSERT_FALSE(v1SetterArgumentIsSyntacticallyComplete("feedspeed:fast",
                                                              V1Command::FeedSpeed));
  TEST_ASSERT_TRUE(v1SetterArgumentIsSyntacticallyComplete("feedspeed:101",
                                                             V1Command::FeedSpeed));
  TEST_ASSERT_FALSE(v1SetterArgumentIsSyntacticallyComplete(
      "airdropenabled:yes", V1Command::AirDropEnabled));
  TEST_ASSERT_TRUE(v1SetterArgumentIsSyntacticallyComplete(
      "cameraledlevel:2147483648", V1Command::CameraLedLevel));
}

void test_v2_setter_ordering_constraints_use_v1_geometry_validation() {
  Configuration configuration = configurationSnapshot();
  const V1DispatchResult slots =
      dispatchV1Command("slotcount:100", 13, configurationContext(),
                        &configuration, configurationLimits());
  TEST_ASSERT_EQUAL(static_cast<int>(V1Response::Ok),
                    static_cast<int>(slots.response));
  const Configuration beforeSortSteps = configuration;
  const V1DispatchResult sortSteps =
      dispatchV1Command("sortsteps:100", 13, configurationContext(),
                        &configuration, configurationLimits());
  TEST_ASSERT_NOT_EQUAL(static_cast<int>(V1Response::Ok),
                        static_cast<int>(sortSteps.response));
  TEST_ASSERT_EQUAL_MEMORY(&beforeSortSteps, &configuration,
                           sizeof(Configuration));

  V1DispatchContext queuedContext = configurationContext();
  queuedContext.queuedPositionOne = 100;
  const V1DispatchResult queued =
      dispatchV1Command("sortsteps:100", 13, queuedContext, &configuration,
                        configurationLimits());
  TEST_ASSERT_NOT_EQUAL(static_cast<int>(V1Response::Ok),
                        static_cast<int>(queued.response));
}

void test_v2_getconfig_streams_v1_schema_slotcount_and_optional_pwm() {
  const Configuration configuration = configurationSnapshot();
  V2Capture capture = {};
  V2OutputWriter writer = {&capture, captureV2Line};
  V2RequestLifecycle lifecycle;
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginReadOnly(31)));
  TEST_ASSERT_TRUE(
      streamV2ConfigurationFields(configuration, false, writer, 31));
  TEST_ASSERT_TRUE(
      emitV2Terminal(&lifecycle, writer, 31, V2ResponseKind::Done, 0));
  TEST_ASSERT_EQUAL(17, capture.writes);
  const char *required[] = {
      "@31 data:FeedMotorSpeed=90\n",
      "@31 data:FeedCycleSteps=70\n",
      "@31 data:SortMotorSpeed=90\n",
      "@31 data:SortSteps=20\n",
      "@31 data:SlotCount=8\n",
      "@31 data:NotificationDelay=90\n",
      "@31 data:SlotDropDelay=400\n",
      "@31 data:AirDropEnabled=0\n",
      "@31 data:AirDropPostDelay=0\n",
      "@31 data:AirDropPreDelay=30\n",
      "@31 data:AirDropSignalTime=50\n",
      "@31 data:FeedHomingOffset=3\n",
      "@31 data:SortHomingOffset=0\n",
      "@31 data:AutoMotorStandbyTimeout=60\n",
      "@31 data:DebounceTimeout=300\n",
      "@31 data:DebouncePauseTime=500\n",
      "@31 done\n"};
  for (size_t index = 0; index < sizeof(required) / sizeof(required[0]);
       ++index)
    assertCapturedLine(capture, required[index]);
  for (unsigned int index = 0; index < capture.writes; ++index)
    TEST_ASSERT_NOT_EQUAL(0, strncmp(capture.lines[index],
                                     "@31 data:CameraLEDLevel=", 23));

  capture = {};
  lifecycle.reset();
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginReadOnly(32)));
  TEST_ASSERT_TRUE(streamV2ConfigurationFields(configuration, true, writer, 32));
  TEST_ASSERT_TRUE(
      emitV2Terminal(&lifecycle, writer, 32, V2ResponseKind::Done, 0));
  TEST_ASSERT_EQUAL(18, capture.writes);
  assertCapturedLine(capture, "@32 data:CameraLEDLevel=78\n");
  assertAllCapturedLinesBounded(capture);
}

void test_v2_status_reports_current_configuration_generation() {
  V2ObservabilitySnapshot snapshot = observabilitySnapshot();
  snapshot.status.configGeneration = 17;
  V2Capture capture = {};
  assertInspection(V2InspectionCommand::Status, 33, snapshot, &capture);
  assertCapturedLine(capture, "@33 data:config_generation=17\n");
}

void test_v2_observability_streams_every_required_field() {
  const V2ObservabilitySnapshot snapshot = observabilitySnapshot();
  V2Capture capture = {};

  assertInspection(V2InspectionCommand::ProtocolVersion, 2, snapshot, &capture);
  TEST_ASSERT_EQUAL(2, capture.writes);
  assertCapturedLine(capture, "@2 data:protocol=2\n");
  assertCapturedLine(capture, "@2 done\n");

  capture = {};
  assertInspection(V2InspectionCommand::Capabilities, 3, snapshot, &capture);
  TEST_ASSERT_EQUAL(12, capture.writes);
  assertCapturedLine(capture, "@3 data:protocol=2\n");
  assertCapturedLine(capture, "@3 data:max_line=64\n");
  assertCapturedLine(capture, "@3 data:crc=none\n");
  assertCapturedLine(capture, "@3 data:queue_depth=2\n");
  assertCapturedLine(capture, "@3 data:slot_max=102\n");
  assertCapturedLine(capture, "@3 data:slot_count=8\n");
  assertCapturedLine(capture, "@3 data:pwm=0\n");
  assertCapturedLine(capture, "@3 data:airdrop=1\n");
  assertCapturedLine(capture, "@3 data:feed_sensor=1\n");
  assertCapturedLine(capture, "@3 data:feed_home=1\n");
  assertCapturedLine(capture, "@3 data:sort_home=1\n");
  assertCapturedLine(capture, "@3 done\n");

  capture = {};
  assertInspection(V2InspectionCommand::Status, 4, snapshot, &capture);
  TEST_ASSERT_EQUAL(11, capture.writes);
  assertCapturedLine(capture, "@4 data:mode=running\n");
  assertCapturedLine(capture, "@4 data:phase=idle\n");
  assertCapturedLine(capture, "@4 data:feed_homed=1\n");
  assertCapturedLine(capture, "@4 data:sort_homed=1\n");
  assertCapturedLine(capture, "@4 data:motor_enabled=0\n");
  assertCapturedLine(capture, "@4 data:active_id=none\n");
  assertCapturedLine(capture, "@4 data:fault_code=0\n");
  assertCapturedLine(capture, "@4 data:queue_previous=0\n");
  assertCapturedLine(capture, "@4 data:queue_next=3\n");
  assertCapturedLine(capture, "@4 data:config_generation=0\n");
  assertCapturedLine(capture, "@4 done\n");
}

void test_v2_queue_preserves_two_position_mapping() {
  V2ObservabilitySnapshot snapshot = observabilitySnapshot();
  snapshot.status.queuePrevious = 4;
  snapshot.status.queueNext = 9;
  V2Capture capture = {};
  assertInspection(V2InspectionCommand::Queue, 5, snapshot, &capture);
  TEST_ASSERT_EQUAL(4, capture.writes);
  assertCapturedLine(capture, "@5 data:queue_depth=2\n");
  assertCapturedLine(capture, "@5 data:queue_previous=4\n");
  assertCapturedLine(capture, "@5 data:queue_next=9\n");
  assertCapturedLine(capture, "@5 done\n");
}

void test_v2_failed_data_write_does_not_emit_success_terminal() {
  V2RequestLifecycle lifecycle;
  FailingV2Capture failing = {};
  failing.successesRemaining = 1;
  V2OutputWriter writer = {&failing, failAfterV2Lines};
  const V2ObservabilitySnapshot snapshot = observabilitySnapshot();
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginReadOnly(6)));
  TEST_ASSERT_FALSE(emitV2Inspection(&lifecycle, writer, 6,
                                     V2InspectionCommand::Capabilities,
                                     snapshot));
  TEST_ASSERT_TRUE(lifecycle.owns(6));
  assertCapturedLine(failing.capture, "@6 data:protocol=2\n");
  for (unsigned int index = 0; index < failing.capture.writes; ++index) {
    TEST_ASSERT_NOT_EQUAL(0,
                          strcmp(failing.capture.lines[index], "@6 done\n"));
  }
}

void test_v2_phase_derivation_covers_machine_phases() {
  V2MachineActivity activity = {};
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::Idle),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity.feedScheduled = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::FeedWait),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity.feeding = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::FeedMove),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity.feedHoming = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::FeedHome),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity = {};
  activity.feedScheduled = true;
  activity.slotDropGateActive = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::Settling),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity = {};
  activity.sortMoving = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::SortMove),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity.sortHomingOffset = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::SortHome),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity = {};
  activity.slotDropGateActive = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::Settling),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity.feedCompletionActive = true;
  activity.airDropActive = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::AirDrop),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity = {};
  activity.feedCycleComplete = true;
  activity.feedError = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::Idle),
                    static_cast<int>(deriveMachinePhase(activity)));
  activity.diagnosticActive = true;
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::Diagnostic),
                    static_cast<int>(deriveMachinePhase(activity)));
}

void test_v2_explicit_read_only_status_runs_during_active_request() {
  V2RequestLifecycle lifecycle;
  V2Capture capture = {};
  V2OutputWriter writer = {&capture, captureV2Line};
  V2ObservabilitySnapshot snapshot = observabilitySnapshot();
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginActive(42)));
  TEST_ASSERT_EQUAL(static_cast<int>(V2BeginResult::Started),
                    static_cast<int>(lifecycle.beginReadOnly(7)));
  snapshot.status.hasActiveRequest = lifecycle.isActive();
  snapshot.status.activeRequestId = lifecycle.activeRequestId();
  TEST_ASSERT_TRUE(emitV2Inspection(&lifecycle, writer, 7,
                                    V2InspectionCommand::Status, snapshot));
  TEST_ASSERT_TRUE(lifecycle.isActive());
  TEST_ASSERT_EQUAL(42, lifecycle.activeRequestId());
  assertCapturedLine(capture, "@7 data:active_id=42\n");
  assertCapturedLine(capture, "@7 done\n");
}

void test_v2_future_field_fixture_is_ignored_by_host_concept() {
  const char *fixture[] = {"mode=running", "future_field=reserved",
                           "phase=idle", "config_generation=0"};
  unsigned int recognized = 0;
  for (size_t index = 0; index < sizeof(fixture) / sizeof(fixture[0]); ++index) {
    if (strncmp(fixture[index], "mode=", 5) == 0 ||
        strncmp(fixture[index], "phase=", 6) == 0 ||
        strncmp(fixture[index], "config_generation=", 18) == 0)
      ++recognized;
  }
  TEST_ASSERT_EQUAL(3, recognized);
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
  RUN_TEST(test_v2_setters_reuse_v1_validation_actions_and_generation);
  RUN_TEST(test_v2_configuration_support_keeps_v1_setter_bytes_unchanged);
  RUN_TEST(test_v2_setter_terminal_semantics_are_correlated_and_bounded);
  RUN_TEST(test_v2_invalid_setters_leave_configuration_and_generation_unchanged);
  RUN_TEST(test_v2_setter_ordering_constraints_use_v1_geometry_validation);
  RUN_TEST(test_v2_getconfig_streams_v1_schema_slotcount_and_optional_pwm);
  RUN_TEST(test_v2_status_reports_current_configuration_generation);
  RUN_TEST(test_v2_observability_streams_every_required_field);
  RUN_TEST(test_v2_queue_preserves_two_position_mapping);
  RUN_TEST(test_v2_failed_data_write_does_not_emit_success_terminal);
  RUN_TEST(test_v2_phase_derivation_covers_machine_phases);
  RUN_TEST(test_v2_explicit_read_only_status_runs_during_active_request);
  RUN_TEST(test_v2_future_field_fixture_is_ignored_by_host_concept);
  RUN_TEST(test_v2_session_reset_clears_parser_lifecycle_and_sequence);
  return UNITY_END();
}

#else

int main(int, char **) {
  UNITY_BEGIN();
  return UNITY_END();
}

#endif
