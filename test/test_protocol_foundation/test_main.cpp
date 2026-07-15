#include <string.h>
#include <stdio.h>

#include <unity.h>

#include "command_parser.h"
#include "protocol.h"

static Configuration defaultConfiguration() {
  Configuration configuration = {90, 70, 90, 20, 8, 90, 400, false, 0, 30,
                                 50, 3,  0,  60, 300, 500, 0};
  return configuration;
}

static V1DispatchLimits limits() {
  V1DispatchLimits result = {32767, UINT32_MAX / 1000UL, 200, 16};
  return result;
}

static V1DispatchContext runningContext() {
  V1DispatchContext result = {true, false, false, 0, 0};
  return result;
}

struct WireCapture {
  char bytes[1024];
  size_t length;
};

static void append(WireCapture *capture, const char *text) {
  const size_t textLength = strlen(text);
  TEST_ASSERT_LESS_OR_EQUAL(sizeof(capture->bytes) - capture->length - 1,
                            textLength);
  memcpy(capture->bytes + capture->length, text, textLength);
  capture->length += textLength;
  capture->bytes[capture->length] = '\0';
}

static void captureResponse(void *context, V1Response response) {
  append(static_cast<WireCapture *>(context), v1ResponseText(response));
}

static void captureConfigurationText(void *context, V1ConfigurationText text) {
  append(static_cast<WireCapture *>(context), v1ConfigurationText(text));
}

static void captureUnsigned(void *context, uint32_t value) {
  char decimal[12];
  snprintf(decimal, sizeof(decimal), "%lu", static_cast<unsigned long>(value));
  append(static_cast<WireCapture *>(context), decimal);
}

static void captureVersion(void *context) {
  append(static_cast<WireCapture *>(context), v1FirmwareVersion());
  append(static_cast<WireCapture *>(context), "\n");
}

static V1OutputWriter captureWriter(WireCapture *capture) {
  V1OutputWriter writer = {capture, captureResponse, captureConfigurationText,
                           captureUnsigned, captureVersion};
  return writer;
}

static V1DispatchResult dispatchAndCapture(V1FrameStatus status,
                                           const char *command, size_t length,
                                           V1DispatchContext context,
                                           Configuration *configuration,
                                           WireCapture *capture) {
  capture->length = 0;
  capture->bytes[0] = '\0';
  const V1DispatchResult result = dispatchV1Frame(
      status, command, length, context, configuration, limits());
  writeV1Output(result, *configuration, false, captureWriter(capture));
  return result;
}

static V1DispatchResult dispatchText(const char *command,
                                     V1DispatchContext context,
                                     Configuration *configuration,
                                     WireCapture *capture) {
  return dispatchAndCapture(V1FrameStatus::Ready, command, strlen(command),
                            context, configuration, capture);
}

static V1DispatchResult dispatchBytes(const char *bytes, size_t length,
                                      V1DispatchContext context,
                                      Configuration *configuration,
                                      WireCapture *capture) {
  CommandParser parser;
  for (size_t index = 0; index < length; ++index) {
    const CommandParser::Result parserResult = parser.consume(bytes[index]);
    if (parserResult == CommandParser::FrameOverflow)
      return dispatchAndCapture(V1FrameStatus::TooLong, 0, 0, context,
                                configuration, capture);
    if (parserResult == CommandParser::FrameInvalid)
      return dispatchAndCapture(V1FrameStatus::Invalid, 0, 0, context,
                                configuration, capture);
    if (parserResult == CommandParser::FrameReady)
      return dispatchAndCapture(V1FrameStatus::Ready, parser.frame(),
                                parser.length(), context, configuration,
                                capture);
  }
  TEST_FAIL_MESSAGE("wire request did not complete a frame");
  return dispatchAndCapture(V1FrameStatus::Invalid, 0, 0, context,
                            configuration, capture);
}

void test_session_resets_to_v1() {
  ProtocolSession session;
  TEST_ASSERT_EQUAL(static_cast<int>(ProtocolMode::V1),
                    static_cast<int>(session.mode()));
  session.reset();
  TEST_ASSERT_EQUAL(static_cast<int>(ProtocolMode::V1),
                    static_cast<int>(session.mode()));
}

void test_startup_inspection_and_negotiation_wire_golden() {
  Configuration configuration = defaultConfiguration();
  WireCapture capture = {{0}, 0};
  const V1DispatchResult startup = {V1Action::None, V1Output::Response,
                                    V1Response::Ready, 0};
  writeV1Output(startup, configuration, false, captureWriter(&capture));
  TEST_ASSERT_EQUAL_STRING("Ready\n", capture.bytes);

  dispatchText("ping", runningContext(), &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING(" ok\n", capture.bytes);
  dispatchText("version", runningContext(), &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING("7.1.260714.6\n", capture.bytes);
  dispatchText("getconfig", runningContext(), &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING(
      "{\"FeedMotorSpeed\":90,\"FeedCycleSteps\":70,\"SortMotorSpeed\":90,"
      "\"SortSteps\":20,\"NotificationDelay\":90,\"SlotDropDelay\":400,"
      "\"AirDropEnabled\":0,\"AirDropPostDelay\":0,\"AirDropPreDelay\":30,"
      "\"AirDropSignalTime\":50,\"FeedHomingOffset\":3,"
      "\"SortHomingOffset\":0,\"AutoMotorStandbyTimeout\":60,"
      "\"DebounceTimeout\":300,\"DebouncePauseTime\":500}\n",
      capture.bytes);
  dispatchText("protocol:2?", runningContext(), &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING("ok\n", capture.bytes);
  dispatchText("protocol:2", runningContext(), &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING("ok\n", capture.bytes);
}

void test_framing_wire_golden() {
  Configuration configuration = defaultConfiguration();
  WireCapture capture;
  dispatchBytes("\n", 1, runningContext(), &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING("ok\n", capture.bytes);
  dispatchBytes("ping\r\n", 6, runningContext(), &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING(" ok\n", capture.bytes);
  char tooLong[42];
  memset(tooLong, 'a', 41);
  tooLong[41] = '\n';
  dispatchBytes(tooLong, sizeof(tooLong), runningContext(), &configuration,
                &capture);
  TEST_ASSERT_EQUAL_STRING("error:command too long\n", capture.bytes);
  const char invalid[] = {'p', 'i', '\0', 'n', 'g', '\n'};
  dispatchBytes(invalid, sizeof(invalid), runningContext(), &configuration,
                &capture);
  TEST_ASSERT_EQUAL_STRING("error:invalid command\n", capture.bytes);
}

void test_every_valid_setter_changes_shared_configuration() {
  struct SetterCase {
    const char *command;
    V1Action action;
  };
  const SetterCase cases[] = {
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
      {"cameraledlevel:79", V1Action::ApplyCameraLedLevel},
  };
  Configuration configuration = defaultConfiguration();
  WireCapture capture;
  for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
    const V1DispatchResult result =
        dispatchText(cases[index].command, runningContext(), &configuration,
                     &capture);
    TEST_ASSERT_EQUAL(static_cast<int>(cases[index].action),
                      static_cast<int>(result.action));
    TEST_ASSERT_EQUAL_STRING("ok\n", capture.bytes);
  }
  TEST_ASSERT_EQUAL(79, configuration.cameraLedLevel);
  dispatchText("getconfig", runningContext(), &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING(
      "{\"FeedMotorSpeed\":80,\"FeedCycleSteps\":71,\"SortMotorSpeed\":81,"
      "\"SortSteps\":19,\"NotificationDelay\":91,\"SlotDropDelay\":401,"
      "\"AirDropEnabled\":1,\"AirDropPostDelay\":1,\"AirDropPreDelay\":31,"
      "\"AirDropSignalTime\":51,\"FeedHomingOffset\":4,"
      "\"SortHomingOffset\":1,\"AutoMotorStandbyTimeout\":61,"
      "\"DebounceTimeout\":301,\"DebouncePauseTime\":501}\n",
      capture.bytes);
}

void test_every_invalid_setter_wire_golden() {
  struct ErrorCase {
    const char *command;
    const char *response;
  };
  const ErrorCase cases[] = {
      {"feedspeed:0", "error:invalid feedspeed\n"},
      {"feedsteps:1001", "error:invalid feedsteps\n"},
      {"feedhomingoffset:201", "error:invalid feedhomingoffset\n"},
      {"sortspeed:101", "error:invalid sortspeed\n"},
      {"sortsteps:0", "error:invalid sortsteps\n"},
      {"sorthomingoffset:201", "error:invalid sorthomingoffset\n"},
      {"slotcount:0", "error:invalid slotcount\n"},
      {"notificationdelay:-1", "error:invalid notificationdelay\n"},
      {"slotdropdelay:-1", "error:invalid slotdropdelay\n"},
      {"automotorstandbytimeout:4294968",
       "error:invalid automotorstandbytimeout\n"},
      {"debounceTimeout:-1", "error:invalid debounceTimeout\n"},
      {"debounceTime:-1", "error:invalid debounceTime\n"},
      {"airdropenabled:yes", "error:invalid airdropenabled\n"},
      {"airdroppredelay:-1", "error:invalid airdroppredelay\n"},
      {"airdroppostdelay:-1", "error:invalid airdroppostdelay\n"},
      {"airdropdsignalduration:-1",
       "error:invalid airdropdsignalduration\n"},
      {"cameraledlevel:not-a-number", "error:invalid cameraledlevel\n"},
  };
  Configuration configuration = defaultConfiguration();
  const Configuration original = configuration;
  WireCapture capture;
  for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
    const V1DispatchResult result =
        dispatchText(cases[index].command, runningContext(), &configuration,
                     &capture);
    TEST_ASSERT_EQUAL(static_cast<int>(V1Action::None),
                      static_cast<int>(result.action));
    TEST_ASSERT_EQUAL_STRING(cases[index].response, capture.bytes);
    TEST_ASSERT_EQUAL_MEMORY(&original, &configuration, sizeof(Configuration));
  }
}

void test_command_actions_and_immediate_output_wire_golden() {
  struct CommandCase {
    const char *command;
    V1Action action;
    const char *response;
  };
  const CommandCase cases[] = {
      {"homefeeder", V1Action::HomeFeeder, "ok\n"},
      {"homesorter", V1Action::HomeSorter, "ok\n"},
      {"sortto:3", V1Action::SortTo, "ok\n"},
      {"test:1", V1Action::StartTest, "testing started\n"},
      {"sorttest:1", V1Action::StartSortTest, "testing started\n"},
      {"stop", V1Action::Stop, "stopped\n"},
      {"3", V1Action::QueueFeed, ""},
      {"xf:3", V1Action::QueueForcedFeed, ""},
  };
  Configuration configuration = defaultConfiguration();
  WireCapture capture;
  for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
    const V1DispatchResult result =
        dispatchText(cases[index].command, runningContext(), &configuration,
                     &capture);
    TEST_ASSERT_EQUAL(static_cast<int>(cases[index].action),
                      static_cast<int>(result.action));
    TEST_ASSERT_EQUAL_STRING(cases[index].response, capture.bytes);
  }
}

void test_invalid_commands_and_state_ordering_wire_golden() {
  struct ErrorCase {
    const char *command;
    const char *response;
  };
  const ErrorCase invalid[] = {
      {"3x", "error:invalid slot\n"},
      {"xf:3x", "error:invalid xf\n"},
      {"sortto:3x", "error:invalid sortto\n"},
      {"test:-1", "error:invalid test\n"},
      {"sorttest:-1", "error:invalid sorttest\n"},
      {"unknown", "ok\n"},
  };
  Configuration configuration = defaultConfiguration();
  WireCapture capture;
  for (size_t index = 0; index < sizeof(invalid) / sizeof(invalid[0]); ++index) {
    dispatchText(invalid[index].command, runningContext(), &configuration,
                 &capture);
    TEST_ASSERT_EQUAL_STRING(invalid[index].response, capture.bytes);
  }

  V1DispatchContext notHomed = runningContext();
  notHomed.running = false;
  dispatchText("3", notHomed, &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING("error:not homed\n", capture.bytes);

  V1DispatchContext busy = runningContext();
  busy.busy = true;
  busy.pendingCommand = true;
  dispatchText("version", busy, &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING("error:busy\n", capture.bytes);
  busy.pendingCommand = false;
  const V1DispatchResult deferred =
      dispatchText("1", busy, &configuration, &capture);
  TEST_ASSERT_EQUAL(static_cast<int>(V1Action::QueuePending),
                    static_cast<int>(deferred.action));
  TEST_ASSERT_EQUAL_STRING("", capture.bytes);
  busy.busy = false;
  busy.pendingCommand = true;
  dispatchText("version", busy, &configuration, &capture);
  TEST_ASSERT_EQUAL_STRING("error:busy\n", capture.bytes);
  const V1DispatchResult stopped =
      dispatchText("stop", busy, &configuration, &capture);
  TEST_ASSERT_EQUAL(static_cast<int>(V1Action::Stop),
                    static_cast<int>(stopped.action));
  TEST_ASSERT_EQUAL_STRING("stopped\n", capture.bytes);
}

void test_asynchronous_wire_literals_are_byte_exact() {
  TEST_ASSERT_EQUAL_STRING("done\n", v1ResponseText(V1Response::Done));
  TEST_ASSERT_EQUAL_STRING("waiting for brass\n",
                           v1ResponseText(V1Response::WaitingForBrass));
  TEST_ASSERT_EQUAL_STRING("error:feed overtravel detected\n",
                           v1ResponseText(V1Response::FeedOvertravel));
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_session_resets_to_v1);
  RUN_TEST(test_startup_inspection_and_negotiation_wire_golden);
  RUN_TEST(test_framing_wire_golden);
  RUN_TEST(test_every_valid_setter_changes_shared_configuration);
  RUN_TEST(test_every_invalid_setter_wire_golden);
  RUN_TEST(test_command_actions_and_immediate_output_wire_golden);
  RUN_TEST(test_invalid_commands_and_state_ordering_wire_golden);
  RUN_TEST(test_asynchronous_wire_literals_are_byte_exact);
  return UNITY_END();
}
