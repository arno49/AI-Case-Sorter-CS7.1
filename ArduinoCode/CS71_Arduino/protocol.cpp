#include "protocol.h"

#include <string.h>

#include "logic.h"

ProtocolSession::ProtocolSession() {
  reset();
}

ProtocolMode ProtocolSession::mode() const {
  return mode_;
}

void ProtocolSession::reset() {
  mode_ = ProtocolMode::V1;
#if PROTOCOL_V2_ENABLED
  activeRequestId_ = 0;
  eventSequence_ = 0;
  crcEnabled_ = false;
#endif
}

#if PROTOCOL_V2_ENABLED
void ProtocolSession::enterV2() {
  mode_ = ProtocolMode::V2;
  activeRequestId_ = 0;
  eventSequence_ = 1;
  crcEnabled_ = false;
}

uint16_t ProtocolSession::activeRequestId() const {
  return activeRequestId_;
}

uint16_t ProtocolSession::eventSequence() const {
  return eventSequence_;
}

bool ProtocolSession::crcEnabled() const {
  return crcEnabled_;
}

static bool isExact(const char *command, size_t length, const char *expected) {
  return command != 0 && strlen(expected) == length &&
         memcmp(command, expected, length) == 0;
}

V2NegotiationAction dispatchV2Negotiation(const char *command, size_t length,
                                          bool executionBusy,
                                          bool pendingCommand) {
  if (isExact(command, length, "protocol:2?")) {
    return executionBusy || pendingCommand ? V2NegotiationAction::Busy
                                           : V2NegotiationAction::Discovery;
  }
  if (isExact(command, length, "protocol:2")) {
    return executionBusy || pendingCommand ? V2NegotiationAction::Busy
                                           : V2NegotiationAction::Activate;
  }
  return V2NegotiationAction::NotHandled;
}

static bool parseV2RequestId(const char *command, size_t length,
                             uint16_t *requestId, size_t *payloadOffset) {
  if (command == 0 || requestId == 0 || payloadOffset == 0) return false;
  *requestId = 0;
  *payloadOffset = 0;
  if (length == 0 || command[0] != '@') return true;

  uint32_t value = 0;
  size_t index = 1;
  const size_t firstDigit = index;
  while (index < length && command[index] >= '0' && command[index] <= '9') {
    value = value * 10U + static_cast<uint8_t>(command[index] - '0');
    if (value > 65535U) return false;
    ++index;
  }
  if (index == firstDigit || value == 0 || index >= length ||
      command[index] != ' ')
    return false;
  *requestId = static_cast<uint16_t>(value);
  *payloadOffset = index + 1;
  return true;
}

V2Protocol1Action dispatchV2Protocol1(const char *command, size_t length,
                                       bool executionBusy,
                                       bool pendingCommand,
                                       uint16_t *requestId) {
  size_t payloadOffset;
  if (!parseV2RequestId(command, length, requestId, &payloadOffset) ||
      !isExact(command + payloadOffset, length - payloadOffset, "protocol:1"))
    return V2Protocol1Action::NotHandled;
  return executionBusy || pendingCommand ? V2Protocol1Action::Busy
                                         : V2Protocol1Action::ReturnToV1;
}

const char *v2DiscoveryResponse() {
  return "protocol:2 available\n";
}

const char *v2ActivationResponse() {
  return "protocol:2 ready\n";
}

static size_t appendV2Text(char *buffer, size_t capacity, size_t length,
                           const char *text) {
  while (*text != '\0' && length + 1 < capacity) {
    buffer[length++] = *text++;
  }
  return length;
}

size_t formatV2Protocol1Response(char *buffer, size_t capacity,
                                 uint16_t requestId, bool busy) {
  if (buffer == 0 || capacity == 0) return 0;
  char digits[5];
  size_t digitCount = 0;
  do {
    digits[digitCount++] = static_cast<char>('0' + requestId % 10U);
    requestId /= 10U;
  } while (requestId != 0 && digitCount < sizeof(digits));

  size_t length = appendV2Text(buffer, capacity, 0, "@");
  while (digitCount > 0 && length + 1 < capacity) {
    buffer[length++] = digits[--digitCount];
  }
  length = appendV2Text(buffer, capacity, length,
                        busy ? " error:2001:busy\n" : " done:protocol=1\n");
  buffer[length] = '\0';
  return length;
}
#endif

const char *v1ResponseText(V1Response response) {
  switch (response) {
#define CS71_V1_RESPONSE_TEXT(name, text) \
  case V1Response::name:                  \
    return text;
    CS71_V1_RESPONSE_LIST(CS71_V1_RESPONSE_TEXT)
#undef CS71_V1_RESPONSE_TEXT
  }
  return "";
}

void ResponseSink::v1(V1Response response) const {
  if (emitV1 != 0) emitV1(context, response);
}

const char *v1ConfigurationText(V1ConfigurationText text) {
  switch (text) {
#define CS71_V1_CONFIGURATION_TEXT(name, value) \
  case V1ConfigurationText::name:               \
    return value;
    CS71_V1_CONFIGURATION_TEXT_LIST(CS71_V1_CONFIGURATION_TEXT)
#undef CS71_V1_CONFIGURATION_TEXT
  }
  return "";
}

const char *v1FirmwareVersion() {
  return CS71_FIRMWARE_VERSION;
}

#if PROTOCOL_V2_ENABLED
void ResponseSink::v2Line(const char *line) const {
  if (emitV2Line != 0) emitV2Line(context, line);
}
#endif

const char *v1CommandValue(const char *command, const char *prefix) {
  if (command == 0 || prefix == 0) {
    return 0;
  }
  const size_t prefixLength = strlen(prefix);
  return strncmp(command, prefix, prefixLength) == 0
             ? command + prefixLength
             : 0;
}

static bool isNumericV1Command(const char *command) {
  return command != 0 &&
         ((command[0] >= '0' && command[0] <= '9') ||
          ((command[0] == '-' || command[0] == '+') &&
           command[1] >= '0' && command[1] <= '9'));
}

static bool hasPrefix(const char *command, const char *prefix) {
  return v1CommandValue(command, prefix) != 0;
}

V1Command classifyV1Command(const char *command) {
  if (isNumericV1Command(command)) return V1Command::NumericPosition;
  if (command == 0) return V1Command::Unknown;
  if (strcmp(command, "stop") == 0) return V1Command::Stop;
  if (strcmp(command, "version") == 0) return V1Command::Version;
  if (strcmp(command, "homefeeder") == 0) return V1Command::HomeFeeder;
  if (strcmp(command, "homesorter") == 0) return V1Command::HomeSorter;
  if (strcmp(command, "getconfig") == 0) return V1Command::GetConfig;
  if (strcmp(command, "ping") == 0) return V1Command::Ping;
  if (hasPrefix(command, "sortto:")) return V1Command::SortTo;
  if (hasPrefix(command, "xf:")) return V1Command::ForceFeed;
  if (hasPrefix(command, "debounceTimeout:")) return V1Command::DebounceTimeout;
  if (hasPrefix(command, "debounceTime:")) return V1Command::DebounceTime;
  if (hasPrefix(command, "feedspeed:")) return V1Command::FeedSpeed;
  if (hasPrefix(command, "feedhomingoffset:")) return V1Command::FeedHomingOffset;
  if (hasPrefix(command, "sorthomingoffset:")) return V1Command::SortHomingOffset;
  if (hasPrefix(command, "sortspeed:")) return V1Command::SortSpeed;
  if (hasPrefix(command, "sortsteps:")) return V1Command::SortSteps;
  if (hasPrefix(command, "slotcount:")) return V1Command::SlotCount;
  if (hasPrefix(command, "feedsteps:")) return V1Command::FeedSteps;
  if (hasPrefix(command, "notificationdelay:")) return V1Command::NotificationDelay;
  if (hasPrefix(command, "slotdropdelay:")) return V1Command::SlotDropDelay;
  if (hasPrefix(command, "airdropenabled:")) return V1Command::AirDropEnabled;
  if (hasPrefix(command, "airdroppostdelay:")) return V1Command::AirDropPostDelay;
  if (hasPrefix(command, "airdroppredelay:")) return V1Command::AirDropPreDelay;
  if (hasPrefix(command, "airdropdsignalduration:"))
    return V1Command::AirDropSignalDuration;
  if (hasPrefix(command, "automotorstandbytimeout:"))
    return V1Command::AutoMotorStandbyTimeout;
  if (hasPrefix(command, "cameraledlevel:")) return V1Command::CameraLedLevel;
  if (hasPrefix(command, "test:")) return V1Command::Test;
  if (hasPrefix(command, "sorttest:")) return V1Command::SortTest;
  return V1Command::Unknown;
}

bool v1CommandRequiresHomedPosition(V1Command command) {
  return command == V1Command::NumericPosition ||
         command == V1Command::ForceFeed || command == V1Command::SortTo ||
         command == V1Command::Test || command == V1Command::SortTest;
}

V1Response v1InvalidResponse(V1Command command) {
  switch (command) {
    case V1Command::NumericPosition: return V1Response::InvalidSlot;
    case V1Command::SortTo: return V1Response::InvalidSortto;
    case V1Command::ForceFeed: return V1Response::InvalidXf;
    case V1Command::DebounceTimeout: return V1Response::InvalidDebounceTimeout;
    case V1Command::DebounceTime: return V1Response::InvalidDebounceTime;
    case V1Command::FeedSpeed: return V1Response::InvalidFeedspeed;
    case V1Command::FeedHomingOffset: return V1Response::InvalidFeedHomingOffset;
    case V1Command::SortHomingOffset: return V1Response::InvalidSortHomingOffset;
    case V1Command::SortSpeed: return V1Response::InvalidSortspeed;
    case V1Command::SortSteps: return V1Response::InvalidSortsteps;
    case V1Command::SlotCount: return V1Response::InvalidSlotcount;
    case V1Command::FeedSteps: return V1Response::InvalidFeedsteps;
    case V1Command::NotificationDelay: return V1Response::InvalidNotificationDelay;
    case V1Command::SlotDropDelay: return V1Response::InvalidSlotDropDelay;
    case V1Command::AirDropEnabled: return V1Response::InvalidAirdropEnabled;
    case V1Command::AirDropPostDelay: return V1Response::InvalidAirdropPostDelay;
    case V1Command::AirDropPreDelay: return V1Response::InvalidAirdropPreDelay;
    case V1Command::AirDropSignalDuration:
      return V1Response::InvalidAirdropSignalDuration;
    case V1Command::AutoMotorStandbyTimeout:
      return V1Response::InvalidAutoMotorStandbyTimeout;
    case V1Command::CameraLedLevel: return V1Response::InvalidCameraLedLevel;
    case V1Command::Test: return V1Response::InvalidTest;
    case V1Command::SortTest: return V1Response::InvalidSorttest;
    default: return V1Response::Ok;
  }
}

static V1DispatchResult result(V1Action action, V1Output output,
                               V1Response response = V1Response::Ok,
                               int32_t value = 0) {
  V1DispatchResult valueResult = {action, output, response, value};
  return valueResult;
}

static bool parseBoundedInt(const char *text, uint32_t minimum,
                            uint32_t maximum, int32_t *value) {
  uint32_t parsed;
  if (!parseUint32(text, maximum, &parsed) || parsed < minimum) return false;
  *value = static_cast<int32_t>(parsed);
  return true;
}

static bool parseSortPosition(const char *text, const Configuration &config,
                              const V1DispatchLimits &limits, int32_t *value) {
  const uint32_t maximum =
      maximumRepresentableSlotCount(config.sortSteps, limits.sortMicrosteps,
                                    limits.maxAvrInt) -
      1UL;
  return parseBoundedInt(text, 0, maximum, value);
}

static V1DispatchResult invalid(V1Command command) {
  return result(V1Action::None, V1Output::Response, v1InvalidResponse(command));
}

V1DispatchResult dispatchV1Command(const char *command, size_t length,
                                   const V1DispatchContext &context,
                                   Configuration *configuration,
                                   const V1DispatchLimits &limits) {
  if (command == 0 || configuration == 0 || length > 40 ||
      memchr(command, '\0', length) != 0) {
    return result(V1Action::None, V1Output::Response,
                  length > 40 ? V1Response::CommandTooLong
                              : V1Response::InvalidCommand);
  }

  const V1Command commandType = classifyV1Command(command);
  if (commandType == V1Command::Stop)
    return result(V1Action::Stop, V1Output::Response, V1Response::Stopped);
  if (!context.running && v1CommandRequiresHomedPosition(commandType))
    return result(V1Action::None, V1Output::Response, V1Response::NotHomed);
  if (context.busy || context.pendingCommand) {
    return context.pendingCommand
               ? result(V1Action::None, V1Output::Response, V1Response::Busy)
               : result(V1Action::QueuePending, V1Output::None);
  }

  int32_t value;
  const char *argument;
  switch (commandType) {
    case V1Command::NumericPosition:
      if (!parseSortPosition(command, *configuration, limits, &value))
        return invalid(commandType);
      return result(V1Action::QueueFeed, V1Output::None, V1Response::Ok, value);
    case V1Command::Version:
      return result(V1Action::None, V1Output::Version);
    case V1Command::HomeFeeder:
      return result(V1Action::HomeFeeder, V1Output::Response);
    case V1Command::HomeSorter:
      return result(V1Action::HomeSorter, V1Output::Response);
    case V1Command::SortTo:
      argument = v1CommandValue(command, "sortto:");
      if (!parseSortPosition(argument, *configuration, limits, &value))
        return invalid(commandType);
      return result(V1Action::SortTo, V1Output::Response, V1Response::Ok, value);
    case V1Command::ForceFeed:
      argument = v1CommandValue(command, "xf:");
      if (!parseSortPosition(argument, *configuration, limits, &value))
        return invalid(commandType);
      return result(V1Action::QueueForcedFeed, V1Output::None, V1Response::Ok,
                    value);
    case V1Command::GetConfig:
      return result(V1Action::None, V1Output::Configuration);
    default:
      break;
  }

  switch (commandType) {
    case V1Command::DebounceTimeout:
      argument = v1CommandValue(command, "debounceTimeout:");
      if (!parseBoundedInt(argument, 0, limits.maxAvrInt, &value)) return invalid(commandType);
      configuration->debounceTimeout = static_cast<uint32_t>(value);
      return result(V1Action::None, V1Output::Response);
    case V1Command::DebounceTime:
      argument = v1CommandValue(command, "debounceTime:");
      if (!parseBoundedInt(argument, 0, limits.maxAvrInt, &value)) return invalid(commandType);
      configuration->debouncePauseTime = static_cast<uint32_t>(value);
      return result(V1Action::None, V1Output::Response);
    case V1Command::FeedSpeed:
      argument = v1CommandValue(command, "feedspeed:");
      if (!parseBoundedInt(argument, 1, 100, &value)) return invalid(commandType);
      configuration->feedSpeed = value;
      return result(V1Action::ApplyFeedSpeed, V1Output::Response, V1Response::Ok, value);
    case V1Command::FeedHomingOffset:
      argument = v1CommandValue(command, "feedhomingoffset:");
      if (!parseBoundedInt(argument, 0, limits.sortFullRevolutionSteps, &value)) return invalid(commandType);
      configuration->feedHomingOffset = value;
      return result(V1Action::ApplyFeedHomingOffset, V1Output::Response, V1Response::Ok, value);
    case V1Command::SortHomingOffset:
      argument = v1CommandValue(command, "sorthomingoffset:");
      if (!parseBoundedInt(argument, 0, limits.sortFullRevolutionSteps, &value)) return invalid(commandType);
      configuration->sortHomingOffset = value;
      return result(V1Action::ApplySortHomingOffset, V1Output::Response, V1Response::Ok, value);
    case V1Command::SortSpeed:
      argument = v1CommandValue(command, "sortspeed:");
      if (!parseBoundedInt(argument, 1, 100, &value)) return invalid(commandType);
      configuration->sortSpeed = value;
      return result(V1Action::ApplySortSpeed, V1Output::Response, V1Response::Ok, value);
    case V1Command::SortSteps: {
      argument = v1CommandValue(command, "sortsteps:");
      if (!parseBoundedInt(argument, 1, 100, &value) ||
          context.queuedPositionOne >
              static_cast<int32_t>(maximumRepresentableSlotCount(
                  value, limits.sortMicrosteps, limits.maxAvrInt) - 1UL) ||
          context.queuedPositionTwo >
              static_cast<int32_t>(maximumRepresentableSlotCount(
                  value, limits.sortMicrosteps, limits.maxAvrInt) - 1UL) ||
          !isSlotCountRepresentable(configuration->slotCount, value,
                                    limits.sortMicrosteps, limits.maxAvrInt))
        return invalid(commandType);
      configuration->sortSteps = value;
      return result(V1Action::None, V1Output::Response);
    }
    case V1Command::SlotCount: {
      argument = v1CommandValue(command, "slotcount:");
      uint32_t slots;
      const uint32_t maximum = maximumRepresentableSlotCount(
          configuration->sortSteps, limits.sortMicrosteps, limits.maxAvrInt);
      if (!parseUint32(argument, maximum, &slots) ||
          !isSlotCountRepresentable(slots, configuration->sortSteps,
                                    limits.sortMicrosteps, limits.maxAvrInt))
        return invalid(commandType);
      configuration->slotCount = slots;
      return result(V1Action::None, V1Output::Response);
    }
    case V1Command::FeedSteps:
      argument = v1CommandValue(command, "feedsteps:");
      if (!parseBoundedInt(argument, 1, 1000, &value)) return invalid(commandType);
      configuration->feedSteps = value;
      return result(V1Action::ApplyFeedSteps, V1Output::Response, V1Response::Ok, value);
    case V1Command::NotificationDelay:
      argument = v1CommandValue(command, "notificationdelay:");
      if (!parseBoundedInt(argument, 0, limits.maxAvrInt, &value)) return invalid(commandType);
      configuration->notificationDelay = value;
      return result(V1Action::None, V1Output::Response);
    case V1Command::SlotDropDelay:
      argument = v1CommandValue(command, "slotdropdelay:");
      if (!parseBoundedInt(argument, 0, limits.maxAvrInt, &value)) return invalid(commandType);
      configuration->slotDropDelay = value;
      return result(V1Action::ApplyDropDelay, V1Output::Response);
    case V1Command::AirDropEnabled: {
      bool enabled;
      argument = v1CommandValue(command, "airdropenabled:");
      if (!parseBool(argument, &enabled)) return invalid(commandType);
      configuration->airDropEnabled = enabled;
      return result(V1Action::ApplyDropDelay, V1Output::Response);
    }
    case V1Command::AirDropPostDelay:
      argument = v1CommandValue(command, "airdroppostdelay:");
      if (!parseBoundedInt(argument, 0, limits.maxAvrInt, &value)) return invalid(commandType);
      configuration->airDropPostDelay = value;
      return result(V1Action::ApplyDropDelay, V1Output::Response);
    case V1Command::AirDropPreDelay:
      argument = v1CommandValue(command, "airdroppredelay:");
      if (!parseBoundedInt(argument, 0, limits.maxAvrInt, &value)) return invalid(commandType);
      configuration->airDropPreDelay = value;
      return result(V1Action::None, V1Output::Response);
    case V1Command::AirDropSignalDuration:
      argument = v1CommandValue(command, "airdropdsignalduration:");
      if (!parseBoundedInt(argument, 0, limits.maxAvrInt, &value)) return invalid(commandType);
      configuration->airDropSignalTime = value;
      return result(V1Action::None, V1Output::Response);
    case V1Command::AutoMotorStandbyTimeout: {
      uint32_t seconds;
      argument = v1CommandValue(command, "automotorstandbytimeout:");
      if (!parseUint32(argument, limits.maxStandbyTimeoutSeconds, &seconds))
        return invalid(commandType);
      configuration->autoMotorStandbyTimeout = seconds;
      return result(V1Action::ApplyAutoMotorStandbyTimeout, V1Output::Response);
    }
    case V1Command::CameraLedLevel:
      argument = v1CommandValue(command, "cameraledlevel:");
      if (!parseInt32(argument, INT32_MIN, INT32_MAX, &value)) return invalid(commandType);
      configuration->cameraLedLevel = clampByte(value);
      return result(V1Action::ApplyCameraLedLevel, V1Output::Response, V1Response::Ok, value);
    case V1Command::Test:
      argument = v1CommandValue(command, "test:");
      if (!parseBoundedInt(argument, 0, limits.maxAvrInt, &value)) return invalid(commandType);
      return result(V1Action::StartTest, V1Output::Response, V1Response::TestingStarted, value);
    case V1Command::SortTest:
      argument = v1CommandValue(command, "sorttest:");
      if (!parseBoundedInt(argument, 0, limits.maxAvrInt, &value)) return invalid(commandType);
      return result(V1Action::StartSortTest, V1Output::Response, V1Response::TestingStarted, value);
    case V1Command::Ping:
      return result(V1Action::None, V1Output::Response, V1Response::Ping);
    default:
      return result(V1Action::None, V1Output::Response);
  }
}

V1DispatchResult dispatchV1Frame(V1FrameStatus status, const char *command,
                                 size_t length,
                                 const V1DispatchContext &context,
                                 Configuration *configuration,
                                 const V1DispatchLimits &limits) {
  if (status == V1FrameStatus::TooLong)
    return result(V1Action::None, V1Output::Response, V1Response::CommandTooLong);
  if (status == V1FrameStatus::Invalid)
    return result(V1Action::None, V1Output::Response, V1Response::InvalidCommand);
  return dispatchV1Command(command, length, context, configuration, limits);
}

void writeV1Output(const V1DispatchResult &output,
                   const Configuration &configuration, bool includeCameraLevel,
                   const V1OutputWriter &writer) {
  if (output.output == V1Output::None) return;
  if (output.output == V1Output::Response && writer.writeResponse != 0) {
    writer.writeResponse(writer.context, output.response);
    return;
  }
  if (output.output == V1Output::Version && writer.writeVersion != 0) {
    writer.writeVersion(writer.context);
    return;
  }
  if (output.output != V1Output::Configuration ||
      writer.writeConfigurationText == 0 || writer.writeUnsigned == 0)
    return;
  writer.writeConfigurationText(writer.context, V1ConfigurationText::Start);
  writer.writeUnsigned(writer.context, configuration.feedSpeed);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::FeedCycleSteps);
  writer.writeUnsigned(writer.context, configuration.feedSteps);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::SortMotorSpeed);
  writer.writeUnsigned(writer.context, configuration.sortSpeed);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::SortSteps);
  writer.writeUnsigned(writer.context, configuration.sortSteps);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::NotificationDelay);
  writer.writeUnsigned(writer.context, configuration.notificationDelay);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::SlotDropDelay);
  writer.writeUnsigned(writer.context, configuration.slotDropDelay);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::AirDropEnabled);
  writer.writeUnsigned(writer.context, configuration.airDropEnabled ? 1U : 0U);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::AirDropPostDelay);
  writer.writeUnsigned(writer.context, configuration.airDropPostDelay);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::AirDropPreDelay);
  writer.writeUnsigned(writer.context, configuration.airDropPreDelay);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::AirDropSignalTime);
  writer.writeUnsigned(writer.context, configuration.airDropSignalTime);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::FeedHomingOffset);
  writer.writeUnsigned(writer.context, configuration.feedHomingOffset);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::SortHomingOffset);
  writer.writeUnsigned(writer.context, configuration.sortHomingOffset);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::AutoMotorStandbyTimeout);
  writer.writeUnsigned(writer.context, configuration.autoMotorStandbyTimeout);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::DebounceTimeout);
  writer.writeUnsigned(writer.context, configuration.debounceTimeout);
  writer.writeConfigurationText(writer.context, V1ConfigurationText::DebouncePauseTime);
  writer.writeUnsigned(writer.context, configuration.debouncePauseTime);
  if (includeCameraLevel) {
    writer.writeConfigurationText(writer.context, V1ConfigurationText::CameraLedLevel);
    writer.writeUnsigned(writer.context, configuration.cameraLedLevel);
  }
  writer.writeConfigurationText(writer.context, V1ConfigurationText::End);
}
