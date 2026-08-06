#include "protocol.h"

#include <string.h>

#include "logic.h"

#if defined(ARDUINO)
#include <avr/pgmspace.h>
#define CS71_V2_TEXT(value) PSTR(value)
#else
#define CS71_V2_TEXT(value) value
#endif

ProtocolSession::ProtocolSession() {
  reset();
}

ProtocolMode ProtocolSession::mode() const {
  return mode_;
}

void ProtocolSession::reset() {
  mode_ = ProtocolMode::V1;
#if PROTOCOL_V2_ENABLED
  eventSequence_ = 0;
  crcEnabled_ = false;
  parser_.reset();
  lifecycle_.reset();
#endif
}

RawStopLineDetector::RawStopLineDetector() {
  reset();
}

bool RawStopLineDetector::consume(char byte) {
  if (state_ == 5) {
    const bool exact = byte == '\n';
    reset();
    return exact;
  }

  if (byte == '\n') {
    const bool exact = state_ == 4;
    reset();
    return exact;
  }

  if (state_ == 4 && byte == '\r') {
    state_ = 5;
    return false;
  }

  if ((state_ == 0 && byte == 's') || (state_ == 1 && byte == 't') ||
      (state_ == 2 && byte == 'o') || (state_ == 3 && byte == 'p')) {
    ++state_;
  } else {
    state_ = 6;
  }
  return false;
}

void RawStopLineDetector::reset() {
  state_ = 0;
}

#if PROTOCOL_V2_ENABLED
void ProtocolSession::enterV2() {
  mode_ = ProtocolMode::V2;
  eventSequence_ = 1;
  crcEnabled_ = false;
  parser_.reset();
  lifecycle_.reset();
}

uint16_t ProtocolSession::activeRequestId() const {
  return lifecycle_.activeRequestId();
}

uint16_t ProtocolSession::eventSequence() const {
  return eventSequence_;
}

uint16_t ProtocolSession::nextEventSequence() {
  const uint16_t sequence = eventSequence_;
  eventSequence_ = eventSequence_ == 65535U ? 1U : eventSequence_ + 1U;
  return sequence;
}

bool ProtocolSession::crcEnabled() const {
  return crcEnabled_;
}

V2FrameParser &ProtocolSession::parser() {
  return parser_;
}

V2RequestLifecycle &ProtocolSession::lifecycle() {
  return lifecycle_;
}

#if !defined(ARDUINO)
bool ProtocolSession::emitEvent(const V2OutputWriter &writer,
                                const char *detail) {
  char line[V2_MAX_LINE_LENGTH + 2];
  if (!formatV2Event(line, sizeof(line), eventSequence_, detail) ||
      writer.writeLine == 0 ||
      !writer.writeLine(writer.context, line, strlen(line)))
    return false;
  nextEventSequence();
  return true;
}
#endif

static bool isExact(const char *command, size_t length, const char *expected) {
#if defined(ARDUINO)
  return command != 0 && strlen_P(expected) == length &&
         memcmp_P(command, expected, length) == 0;
#else
  return command != 0 && strlen(expected) == length &&
         memcmp(command, expected, length) == 0;
#endif
}

V2NegotiationAction dispatchV2Negotiation(const char *command, size_t length,
                                          bool executionBusy,
                                          bool pendingCommand) {
  if (isExact(command, length, CS71_V2_TEXT("protocol:2?"))) {
    return executionBusy || pendingCommand ? V2NegotiationAction::Busy
                                           : V2NegotiationAction::Discovery;
  }
  if (isExact(command, length, CS71_V2_TEXT("protocol:2"))) {
    return executionBusy || pendingCommand ? V2NegotiationAction::Busy
                                           : V2NegotiationAction::Activate;
  }
  return V2NegotiationAction::NotHandled;
}

V2EnvelopeStatus parseV2RequestEnvelope(const char *frame, size_t length,
                                        V2RequestEnvelope *envelope) {
  if (frame == 0 || envelope == 0 || length == 0 ||
      length > V2_MAX_LINE_LENGTH)
    return V2EnvelopeStatus::BadFrame;

  for (size_t index = 0; index < length; ++index) {
    const unsigned char value = static_cast<unsigned char>(frame[index]);
    if (value < 0x20U || value > 0x7eU) return V2EnvelopeStatus::BadFrame;
  }

  envelope->requestId = 0;
  envelope->payload = frame;
  envelope->payloadLength = length;
  envelope->explicitId = false;
  if (frame[0] != '@') {
    return frame[0] == ' ' || frame[length - 1] == ' '
               ? V2EnvelopeStatus::BadFrame
               : V2EnvelopeStatus::Ready;
  }

  uint32_t value = 0;
  size_t index = 1;
  const size_t firstDigit = index;
  while (index < length && frame[index] >= '0' && frame[index] <= '9') {
    value = value * 10U + static_cast<uint8_t>(frame[index] - '0');
    if (value > 65535U) return V2EnvelopeStatus::BadId;
    ++index;
  }
  if (index == firstDigit || value == 0 || index >= length ||
      frame[index] != ' ')
    return V2EnvelopeStatus::BadId;

  ++index;
  if (index == length || frame[index] == ' ' || frame[length - 1] == ' ')
    return V2EnvelopeStatus::BadFrame;
  envelope->requestId = static_cast<uint16_t>(value);
  envelope->payload = frame + index;
  envelope->payloadLength = length - index;
  envelope->explicitId = true;
  return V2EnvelopeStatus::Ready;
}

V2Protocol1Action dispatchV2Protocol1(const char *command, size_t length,
                                       bool executionBusy,
                                       bool pendingCommand,
                                       uint16_t *requestId) {
  V2RequestEnvelope envelope;
  if (requestId == 0 ||
      parseV2RequestEnvelope(command, length, &envelope) !=
          V2EnvelopeStatus::Ready ||
      !isExact(envelope.payload, envelope.payloadLength,
               CS71_V2_TEXT("protocol:1")))
    return V2Protocol1Action::NotHandled;
  *requestId = envelope.requestId;
  return executionBusy || pendingCommand ? V2Protocol1Action::Busy
                                         : V2Protocol1Action::ReturnToV1;
}

const char *v2DiscoveryResponse() {
  return "protocol:2 available\n";
}

const char *v2ActivationResponse() {
  return "protocol:2 ready\n";
}

V2RequestLifecycle::V2RequestLifecycle() {
  reset();
}

void V2RequestLifecycle::reset() {
  active_ = false;
  activeRequestId_ = 0;
  readOnly_ = false;
  readOnlyRequestId_ = 0;
}

V2BeginResult V2RequestLifecycle::beginActive(uint16_t requestId) {
  if (requestId == 0 &&
      ((active_ && activeRequestId_ == 0) ||
       (readOnly_ && readOnlyRequestId_ == 0)))
    return V2BeginResult::Busy;
  if (active_ && activeRequestId_ == requestId)
    return V2BeginResult::DuplicateId;
  if (readOnly_ && readOnlyRequestId_ == requestId)
    return V2BeginResult::DuplicateId;
  if (active_) return V2BeginResult::Busy;
  active_ = true;
  activeRequestId_ = requestId;
  return V2BeginResult::Started;
}

V2BeginResult V2RequestLifecycle::beginReadOnly(uint16_t requestId) {
  if (requestId == 0 &&
      ((active_ && activeRequestId_ == 0) ||
       (readOnly_ && readOnlyRequestId_ == 0)))
    return V2BeginResult::Busy;
  if ((active_ && activeRequestId_ == requestId) ||
      (readOnly_ && readOnlyRequestId_ == requestId))
    return V2BeginResult::DuplicateId;
  if (readOnly_) return V2BeginResult::Busy;
  readOnly_ = true;
  readOnlyRequestId_ = requestId;
  return V2BeginResult::Started;
}

bool V2RequestLifecycle::isActive() const {
  return active_;
}

uint16_t V2RequestLifecycle::activeRequestId() const {
  return active_ ? activeRequestId_ : 0;
}

bool V2RequestLifecycle::isIdlessActive() const {
  return active_ && activeRequestId_ == 0;
}

bool V2RequestLifecycle::owns(uint16_t requestId) const {
  return (active_ && activeRequestId_ == requestId) ||
         (readOnly_ && readOnlyRequestId_ == requestId);
}

bool V2RequestLifecycle::terminal(uint16_t requestId) {
  if (active_ && activeRequestId_ == requestId) {
    active_ = false;
    activeRequestId_ = 0;
    return true;
  }
  if (readOnly_ && readOnlyRequestId_ == requestId) {
    readOnly_ = false;
    readOnlyRequestId_ = 0;
    return true;
  }
  return false;
}

V2StopCancellation V2RequestLifecycle::cancel() {
  V2StopCancellation cancellation = {V2StopResult::Stopped, active_,
                                     activeRequestId_};
  reset();
  return cancellation;
}

V2StopCancellation V2RequestLifecycle::cancelForStop(uint16_t stopRequestId) {
  V2StopCancellation cancellation = {V2StopResult::Stopped, false, 0};
  if (owns(stopRequestId)) {
    cancellation.result = V2StopResult::DuplicateId;
    return cancellation;
  }
  return cancel();
}

#if !defined(ARDUINO)
static bool appendV2Char(char *buffer, size_t capacity, size_t *length,
                         char value) {
  if (*length >= V2_MAX_LINE_LENGTH || *length + 1 >= capacity) return false;
  buffer[(*length)++] = value;
  return true;
}

static bool appendV2Text(char *buffer, size_t capacity, size_t *length,
                         const char *text) {
  if (text == 0) return false;
  while (*text != '\0') {
    const unsigned char value = static_cast<unsigned char>(*text);
    if (value < 0x20U || value > 0x7eU ||
        !appendV2Char(buffer, capacity, length, *text))
      return false;
    ++text;
  }
  return true;
}

static bool appendV2Unsigned(char *buffer, size_t capacity, size_t *length,
                             uint16_t value) {
  char digits[5];
  size_t count = 0;
  do {
    digits[count++] = static_cast<char>('0' + value % 10U);
    value /= 10U;
  } while (value != 0);
  while (count > 0) {
    if (!appendV2Char(buffer, capacity, length, digits[--count])) return false;
  }
  return true;
}

static bool appendV2Unsigned32(char *buffer, size_t capacity, size_t *length,
                               uint32_t value) {
  char digits[10];
  size_t count = 0;
  do {
    digits[count++] = static_cast<char>('0' + value % 10U);
    value /= 10U;
  } while (value != 0);
  while (count > 0) {
    if (!appendV2Char(buffer, capacity, length, digits[--count])) return false;
  }
  return true;
}

static bool finishV2Line(char *buffer, size_t capacity, const char *line,
                        size_t length) {
  if (length > V2_MAX_LINE_LENGTH || length + 2 > capacity) return false;
  memcpy(buffer, line, length);
  buffer[length] = '\n';
  buffer[length + 1] = '\0';
  return true;
}

bool formatV2Response(char *buffer, size_t capacity, uint16_t requestId,
                      V2ResponseKind kind, const char *detail) {
  if (buffer == 0 || capacity == 0) return false;
  buffer[0] = '\0';
  char line[V2_MAX_LINE_LENGTH + 1];
  size_t length = 0;
  if (!appendV2Char(line, sizeof(line), &length, '@') ||
      !appendV2Unsigned(line, sizeof(line), &length, requestId) ||
      !appendV2Char(line, sizeof(line), &length, ' '))
    return false;

  const char *prefix = "";
  bool needsDetail = false;
  bool colonBeforeDetail = false;
  switch (kind) {
    case V2ResponseKind::Accepted:
      prefix = "accepted";
      colonBeforeDetail = detail != 0 && detail[0] != '\0';
      break;
    case V2ResponseKind::Progress:
      prefix = "progress:";
      needsDetail = true;
      break;
    case V2ResponseKind::Data:
      prefix = "data:";
      needsDetail = true;
      break;
    case V2ResponseKind::Done:
      prefix = "done";
      colonBeforeDetail = detail != 0 && detail[0] != '\0';
      break;
    case V2ResponseKind::Error:
      prefix = "error:";
      needsDetail = true;
      break;
  }
  if ((needsDetail && (detail == 0 || detail[0] == '\0')) ||
      !appendV2Text(line, sizeof(line), &length, prefix) ||
      (colonBeforeDetail &&
       !appendV2Char(line, sizeof(line), &length, ':')) ||
      ((detail != 0 && detail[0] != '\0') &&
       !appendV2Text(line, sizeof(line), &length, detail)))
    return false;
  return finishV2Line(buffer, capacity, line, length);
}

bool formatV2Event(char *buffer, size_t capacity, uint16_t sequence,
                   const char *detail) {
  if (buffer == 0 || capacity == 0 || sequence == 0 || detail == 0 ||
      detail[0] == '\0')
    return false;
  buffer[0] = '\0';
  char line[V2_MAX_LINE_LENGTH + 1];
  size_t length = 0;
  if (!appendV2Char(line, sizeof(line), &length, '!') ||
      !appendV2Unsigned(line, sizeof(line), &length, sequence) ||
      !appendV2Char(line, sizeof(line), &length, ' ') ||
      !appendV2Text(line, sizeof(line), &length, detail))
    return false;
  return finishV2Line(buffer, capacity, line, length);
}

bool emitV2Response(const V2OutputWriter &writer, uint16_t requestId,
                    V2ResponseKind kind, const char *detail) {
  char line[V2_MAX_LINE_LENGTH + 2];
  return writer.writeLine != 0 &&
         formatV2Response(line, sizeof(line), requestId, kind, detail) &&
         writer.writeLine(writer.context, line, strlen(line));
}

bool emitV2Terminal(V2RequestLifecycle *lifecycle,
                    const V2OutputWriter &writer, uint16_t requestId,
                    V2ResponseKind kind, const char *detail) {
  if (lifecycle == 0 || !lifecycle->owns(requestId) ||
      (kind != V2ResponseKind::Done && kind != V2ResponseKind::Error) ||
      !emitV2Response(writer, requestId, kind, detail))
    return false;
  return lifecycle->terminal(requestId);
}
#endif

V2InspectionCommand classifyV2InspectionCommand(const char *payload,
                                                size_t length) {
  if (isExact(payload, length, CS71_V2_TEXT("protocolversion")))
    return V2InspectionCommand::ProtocolVersion;
  if (isExact(payload, length, CS71_V2_TEXT("capabilities")))
    return V2InspectionCommand::Capabilities;
  if (isExact(payload, length, CS71_V2_TEXT("status")))
    return V2InspectionCommand::Status;
  if (isExact(payload, length, CS71_V2_TEXT("queue")))
    return V2InspectionCommand::Queue;
  return V2InspectionCommand::None;
}

MachinePhase deriveMachinePhase(const V2MachineActivity &activity) {
  if (activity.diagnosticActive) return MachinePhase::Diagnostic;
  if (activity.feedCompletionActive)
    return activity.airDropActive ? MachinePhase::AirDrop
                                  : MachinePhase::Settling;
  if (activity.feedHoming || activity.feedHomingOffset)
    return MachinePhase::FeedHome;
  if (activity.sortHoming || activity.sortHomingOffset || activity.sorterJogActive)
    return MachinePhase::SortHome;
  if (activity.sortMoving) return MachinePhase::SortMove;
  if (activity.slotDropGateActive) return MachinePhase::Settling;
  if (activity.feeding) return MachinePhase::FeedMove;
  if (activity.feedScheduled) return MachinePhase::FeedWait;
  if (activity.feedCycleInProgress ||
      (activity.feedCycleComplete && !activity.feedError))
    return MachinePhase::Settling;
  return MachinePhase::Idle;
}

#if !defined(ARDUINO)
static bool formatV2LiteralField(char *buffer, size_t capacity,
                                 const char *key, const char *value) {
  if (buffer == 0 || capacity == 0 || key == 0 || value == 0) return false;
  size_t length = 0;
  buffer[0] = '\0';
  return appendV2Text(buffer, capacity, &length, key) &&
         appendV2Char(buffer, capacity, &length, '=') &&
         appendV2Text(buffer, capacity, &length, value) &&
         (length < capacity ? (buffer[length] = '\0', true) : false);
}

static bool formatV2UnsignedField(char *buffer, size_t capacity,
                                  const char *key, uint32_t value) {
  if (buffer == 0 || capacity == 0 || key == 0) return false;
  size_t length = 0;
  buffer[0] = '\0';
  return appendV2Text(buffer, capacity, &length, key) &&
         appendV2Char(buffer, capacity, &length, '=') &&
         appendV2Unsigned32(buffer, capacity, &length, value) &&
         (length < capacity ? (buffer[length] = '\0', true) : false);
}

static bool formatProtocolVersion(char *buffer, size_t capacity,
                                  const V2ObservabilitySnapshot &) {
  return formatV2LiteralField(buffer, capacity, "protocol", "2");
}

static bool formatCapabilitiesProtocol(char *buffer, size_t capacity,
                                       const V2ObservabilitySnapshot &) {
  return formatV2LiteralField(buffer, capacity, "protocol", "2");
}

static bool formatCapabilitiesMaxLine(char *buffer, size_t capacity,
                                      const V2ObservabilitySnapshot &) {
  return formatV2UnsignedField(buffer, capacity, "max_line", V2_MAX_LINE_LENGTH);
}

static bool formatCapabilitiesCrc(char *buffer, size_t capacity,
                                  const V2ObservabilitySnapshot &) {
  return formatV2LiteralField(buffer, capacity, "crc", "none");
}

static bool formatCapabilitiesQueueDepth(char *buffer, size_t capacity,
                                         const V2ObservabilitySnapshot &) {
  return formatV2UnsignedField(buffer, capacity, "queue_depth", 2);
}

static bool formatCapabilitiesSlotMax(char *buffer, size_t capacity,
                                      const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "slot_max",
                               snapshot.capabilities.slotMax);
}

static bool formatCapabilitiesSlotCount(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "slot_count",
                               snapshot.capabilities.slotCount);
}

static bool formatCapabilitiesPwm(char *buffer, size_t capacity,
                                  const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "pwm",
                               snapshot.capabilities.pwm ? 1 : 0);
}

static bool formatCapabilitiesAirDrop(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "airdrop",
                               snapshot.capabilities.airDrop ? 1 : 0);
}

static bool formatCapabilitiesFeedSensor(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "feed_sensor",
                               snapshot.capabilities.feedSensor ? 1 : 0);
}

static bool formatCapabilitiesFeedHome(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "feed_home",
                               snapshot.capabilities.feedHome ? 1 : 0);
}

static bool formatCapabilitiesSortHome(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "sort_home",
                               snapshot.capabilities.sortHome ? 1 : 0);
}

static const char *machineModeName(MachineMode mode) {
  switch (mode) {
    case MachineMode::Running:
      return "running";
    case MachineMode::Recovering:
      return "recovering";
    case MachineMode::Stopped:
      return "stopped";
  }
  return "recovering";
}

static const char *machinePhaseName(MachinePhase phase) {
  switch (phase) {
    case MachinePhase::Idle:
      return "idle";
    case MachinePhase::FeedWait:
      return "feed_wait";
    case MachinePhase::FeedMove:
      return "feed_move";
    case MachinePhase::FeedHome:
      return "feed_home";
    case MachinePhase::SortMove:
      return "sort_move";
    case MachinePhase::SortHome:
      return "sort_home";
    case MachinePhase::Settling:
      return "settling";
    case MachinePhase::AirDrop:
      return "airdrop";
    case MachinePhase::Diagnostic:
      return "diagnostic";
  }
  return "idle";
}

static bool formatStatusMode(char *buffer, size_t capacity,
                             const V2ObservabilitySnapshot &snapshot) {
  return formatV2LiteralField(buffer, capacity, "mode",
                              machineModeName(snapshot.status.mode));
}

static bool formatStatusPhase(char *buffer, size_t capacity,
                              const V2ObservabilitySnapshot &snapshot) {
  return formatV2LiteralField(buffer, capacity, "phase",
                              machinePhaseName(snapshot.status.phase));
}

static bool formatStatusFeedHomed(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "feed_homed",
                               snapshot.status.feedHomed ? 1 : 0);
}

static bool formatStatusSortHomed(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "sort_homed",
                               snapshot.status.sortHomed ? 1 : 0);
}

static bool formatStatusMotorEnabled(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "motor_enabled",
                               snapshot.status.motorEnabled ? 1 : 0);
}

static bool formatStatusActiveId(char *buffer, size_t capacity,
                                 const V2ObservabilitySnapshot &snapshot) {
  return snapshot.status.hasActiveRequest
             ? formatV2UnsignedField(buffer, capacity, "active_id",
                                     snapshot.status.activeRequestId)
             : formatV2LiteralField(buffer, capacity, "active_id", "none");
}

static bool formatStatusFaultCode(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "fault_code",
                               snapshot.status.faultCode);
}

static bool formatQueuePrevious(char *buffer, size_t capacity,
                                const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "queue_previous",
                               snapshot.status.queuePrevious);
}

static bool formatQueueNext(char *buffer, size_t capacity,
                            const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "queue_next",
                               snapshot.status.queueNext);
}

static bool formatStatusConfigGeneration(
    char *buffer, size_t capacity, const V2ObservabilitySnapshot &snapshot) {
  return formatV2UnsignedField(buffer, capacity, "config_generation",
                               snapshot.status.configGeneration);
}

bool streamV2InspectionFields(V2InspectionCommand command,
                              const V2ObservabilitySnapshot &snapshot,
                              const V2OutputWriter &writer,
                              uint16_t requestId) {
#define CS71_STREAM_V2_FIELD(format)                                           \
  do {                                                                         \
    char detail[V2_MAX_LINE_LENGTH + 1];                                       \
    if (!format(detail, sizeof(detail), snapshot) ||                           \
        !emitV2Response(writer, requestId, V2ResponseKind::Data, detail))     \
      return false;                                                            \
  } while (false)
  switch (command) {
    case V2InspectionCommand::ProtocolVersion:
      CS71_STREAM_V2_FIELD(formatProtocolVersion);
      return true;
    case V2InspectionCommand::Capabilities:
      CS71_STREAM_V2_FIELD(formatCapabilitiesProtocol);
      CS71_STREAM_V2_FIELD(formatCapabilitiesMaxLine);
      CS71_STREAM_V2_FIELD(formatCapabilitiesCrc);
      CS71_STREAM_V2_FIELD(formatCapabilitiesQueueDepth);
      CS71_STREAM_V2_FIELD(formatCapabilitiesSlotMax);
      CS71_STREAM_V2_FIELD(formatCapabilitiesSlotCount);
      CS71_STREAM_V2_FIELD(formatCapabilitiesPwm);
      CS71_STREAM_V2_FIELD(formatCapabilitiesAirDrop);
      CS71_STREAM_V2_FIELD(formatCapabilitiesFeedSensor);
      CS71_STREAM_V2_FIELD(formatCapabilitiesFeedHome);
      CS71_STREAM_V2_FIELD(formatCapabilitiesSortHome);
      return true;
    case V2InspectionCommand::Status:
      CS71_STREAM_V2_FIELD(formatStatusMode);
      CS71_STREAM_V2_FIELD(formatStatusPhase);
      CS71_STREAM_V2_FIELD(formatStatusFeedHomed);
      CS71_STREAM_V2_FIELD(formatStatusSortHomed);
      CS71_STREAM_V2_FIELD(formatStatusMotorEnabled);
      CS71_STREAM_V2_FIELD(formatStatusActiveId);
      CS71_STREAM_V2_FIELD(formatStatusFaultCode);
      CS71_STREAM_V2_FIELD(formatQueuePrevious);
      CS71_STREAM_V2_FIELD(formatQueueNext);
      CS71_STREAM_V2_FIELD(formatStatusConfigGeneration);
      return true;
    case V2InspectionCommand::Queue:
      CS71_STREAM_V2_FIELD(formatCapabilitiesQueueDepth);
      CS71_STREAM_V2_FIELD(formatQueuePrevious);
      CS71_STREAM_V2_FIELD(formatQueueNext);
      return true;
    case V2InspectionCommand::None:
#undef CS71_STREAM_V2_FIELD
      return false;
  }
#undef CS71_STREAM_V2_FIELD
  return true;
}

bool emitV2Inspection(V2RequestLifecycle *lifecycle,
                      const V2OutputWriter &writer, uint16_t requestId,
                      V2InspectionCommand command,
                      const V2ObservabilitySnapshot &snapshot) {
  if (!streamV2InspectionFields(command, snapshot, writer, requestId))
    return emitV2Terminal(lifecycle, writer, requestId, V2ResponseKind::Error,
                          "1005:internal");
  return emitV2Terminal(lifecycle, writer, requestId, V2ResponseKind::Done, 0);
}

bool streamV2ConfigurationFields(const Configuration &configuration,
                                bool includeCameraLevel,
                                const V2OutputWriter &writer,
                                uint16_t requestId) {
#define CS71_STREAM_V2_CONFIGURATION_FIELD(name, value)                         \
  do {                                                                           \
    char detail[V2_MAX_LINE_LENGTH + 1];                                         \
    if (!formatV2UnsignedField(detail, sizeof(detail), name,                    \
                               static_cast<uint32_t>(value)) ||                 \
        !emitV2Response(writer, requestId, V2ResponseKind::Data, detail))       \
      return false;                                                              \
  } while (false)
  CS71_STREAM_V2_CONFIGURATION_FIELD("FeedMotorSpeed", configuration.feedSpeed);
  CS71_STREAM_V2_CONFIGURATION_FIELD("FeedCycleSteps", configuration.feedSteps);
  CS71_STREAM_V2_CONFIGURATION_FIELD("SortMotorSpeed", configuration.sortSpeed);
  CS71_STREAM_V2_CONFIGURATION_FIELD("SortSteps", configuration.sortSteps);
  CS71_STREAM_V2_CONFIGURATION_FIELD("SlotCount", configuration.slotCount);
  CS71_STREAM_V2_CONFIGURATION_FIELD("NotificationDelay",
                                    configuration.notificationDelay);
  CS71_STREAM_V2_CONFIGURATION_FIELD("SlotDropDelay",
                                    configuration.slotDropDelay);
  CS71_STREAM_V2_CONFIGURATION_FIELD("AirDropEnabled",
                                    configuration.airDropEnabled ? 1 : 0);
  CS71_STREAM_V2_CONFIGURATION_FIELD("AirDropPostDelay",
                                    configuration.airDropPostDelay);
  CS71_STREAM_V2_CONFIGURATION_FIELD("AirDropPreDelay",
                                    configuration.airDropPreDelay);
  CS71_STREAM_V2_CONFIGURATION_FIELD("AirDropSignalTime",
                                    configuration.airDropSignalTime);
  CS71_STREAM_V2_CONFIGURATION_FIELD("FeedHomingOffset",
                                    configuration.feedHomingOffset);
  CS71_STREAM_V2_CONFIGURATION_FIELD("SortHomingOffset",
                                    configuration.sortHomingOffset);
  CS71_STREAM_V2_CONFIGURATION_FIELD("AutoMotorStandbyTimeout",
                                    configuration.autoMotorStandbyTimeout);
  CS71_STREAM_V2_CONFIGURATION_FIELD("DebounceTimeout",
                                    configuration.debounceTimeout);
  CS71_STREAM_V2_CONFIGURATION_FIELD("DebouncePauseTime",
                                    configuration.debouncePauseTime);
  if (includeCameraLevel)
    CS71_STREAM_V2_CONFIGURATION_FIELD("CameraLEDLevel",
                                      configuration.cameraLedLevel);
#undef CS71_STREAM_V2_CONFIGURATION_FIELD
  return true;
}

size_t formatV2Protocol1Response(char *buffer, size_t capacity,
                                uint16_t requestId, bool busy) {
  return formatV2Response(buffer, capacity, requestId,
                         busy ? V2ResponseKind::Error
                              : V2ResponseKind::Done,
                         busy ? "2001:busy" : "protocol=1")
             ? strlen(buffer)
             : 0;
}
#endif
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

#if PROTOCOL_V2_ENABLED && !defined(ARDUINO)
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

bool v1CommandIsSetter(V1Command command) {
  return command >= V1Command::DebounceTimeout &&
         command <= V1Command::CameraLedLevel;
}

static const char *v1SetterArgument(const char *command, V1Command commandType) {
  switch (commandType) {
    case V1Command::DebounceTimeout:
      return v1CommandValue(command, "debounceTimeout:");
    case V1Command::DebounceTime:
      return v1CommandValue(command, "debounceTime:");
    case V1Command::FeedSpeed:
      return v1CommandValue(command, "feedspeed:");
    case V1Command::FeedHomingOffset:
      return v1CommandValue(command, "feedhomingoffset:");
    case V1Command::SortHomingOffset:
      return v1CommandValue(command, "sorthomingoffset:");
    case V1Command::SortSpeed:
      return v1CommandValue(command, "sortspeed:");
    case V1Command::SortSteps:
      return v1CommandValue(command, "sortsteps:");
    case V1Command::SlotCount:
      return v1CommandValue(command, "slotcount:");
    case V1Command::FeedSteps:
      return v1CommandValue(command, "feedsteps:");
    case V1Command::NotificationDelay:
      return v1CommandValue(command, "notificationdelay:");
    case V1Command::SlotDropDelay:
      return v1CommandValue(command, "slotdropdelay:");
    case V1Command::AirDropEnabled:
      return v1CommandValue(command, "airdropenabled:");
    case V1Command::AirDropPostDelay:
      return v1CommandValue(command, "airdroppostdelay:");
    case V1Command::AirDropPreDelay:
      return v1CommandValue(command, "airdroppredelay:");
    case V1Command::AirDropSignalDuration:
      return v1CommandValue(command, "airdropdsignalduration:");
    case V1Command::AutoMotorStandbyTimeout:
      return v1CommandValue(command, "automotorstandbytimeout:");
    case V1Command::CameraLedLevel:
      return v1CommandValue(command, "cameraledlevel:");
    default:
      return 0;
  }
}

static bool isCompleteDecimal(const char *text) {
  if (text == 0 || *text == '\0') return false;
  if (*text == '+' || *text == '-') ++text;
  if (*text == '\0') return false;
  while (*text != '\0') {
    if (*text < '0' || *text > '9') return false;
    ++text;
  }
  return true;
}

bool v1SetterArgumentIsSyntacticallyComplete(const char *command,
                                             V1Command commandType) {
  const char *argument = v1SetterArgument(command, commandType);
  if (commandType == V1Command::AirDropEnabled) {
    bool value;
    return parseBool(argument, &value);
  }
  return isCompleteDecimal(argument);
}

bool v1SetterRange(V1Command command, const Configuration &configuration,
                   const V1DispatchLimits &limits, V1SetterRange *range) {
  if (range == 0) return false;
  switch (command) {
    case V1Command::FeedSpeed:
    case V1Command::SortSpeed:
    case V1Command::SortSteps:
      range->minimum = 1;
      range->maximum = 100;
      return true;
    case V1Command::FeedSteps:
      range->minimum = 1;
      range->maximum = 1000;
      return true;
    case V1Command::FeedHomingOffset:
    case V1Command::SortHomingOffset:
      range->minimum = 0;
      range->maximum = limits.sortFullRevolutionSteps;
      return true;
    case V1Command::SlotCount:
      range->minimum = 1;
      range->maximum = maximumRepresentableSlotCount(
          configuration.sortSteps, limits.sortMicrosteps, limits.maxAvrInt);
      return true;
    case V1Command::DebounceTimeout:
    case V1Command::DebounceTime:
    case V1Command::NotificationDelay:
    case V1Command::SlotDropDelay:
    case V1Command::AirDropPostDelay:
    case V1Command::AirDropPreDelay:
    case V1Command::AirDropSignalDuration:
      range->minimum = 0;
      range->maximum = limits.maxAvrInt;
      return true;
    case V1Command::AutoMotorStandbyTimeout:
      range->minimum = 0;
      range->maximum = limits.maxStandbyTimeoutSeconds;
      return true;
    default:
      return false;
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
  V1SetterRange setterRange;
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
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->debounceTimeout = static_cast<uint32_t>(value);
      return result(V1Action::None, V1Output::Response);
    case V1Command::DebounceTime:
      argument = v1CommandValue(command, "debounceTime:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->debouncePauseTime = static_cast<uint32_t>(value);
      return result(V1Action::None, V1Output::Response);
    case V1Command::FeedSpeed:
      argument = v1CommandValue(command, "feedspeed:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->feedSpeed = value;
      return result(V1Action::ApplyFeedSpeed, V1Output::Response, V1Response::Ok, value);
    case V1Command::FeedHomingOffset:
      argument = v1CommandValue(command, "feedhomingoffset:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->feedHomingOffset = value;
      return result(V1Action::ApplyFeedHomingOffset, V1Output::Response, V1Response::Ok, value);
    case V1Command::SortHomingOffset:
      argument = v1CommandValue(command, "sorthomingoffset:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->sortHomingOffset = value;
      return result(V1Action::ApplySortHomingOffset, V1Output::Response, V1Response::Ok, value);
    case V1Command::SortSpeed:
      argument = v1CommandValue(command, "sortspeed:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->sortSpeed = value;
      return result(V1Action::ApplySortSpeed, V1Output::Response, V1Response::Ok, value);
    case V1Command::SortSteps: {
      argument = v1CommandValue(command, "sortsteps:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value) ||
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
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseUint32(argument, setterRange.maximum, &slots) ||
          !isSlotCountRepresentable(slots, configuration->sortSteps,
                                    limits.sortMicrosteps, limits.maxAvrInt))
        return invalid(commandType);
      configuration->slotCount = slots;
      return result(V1Action::None, V1Output::Response);
    }
    case V1Command::FeedSteps:
      argument = v1CommandValue(command, "feedsteps:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->feedSteps = value;
      return result(V1Action::ApplyFeedSteps, V1Output::Response, V1Response::Ok, value);
    case V1Command::NotificationDelay:
      argument = v1CommandValue(command, "notificationdelay:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->notificationDelay = value;
      return result(V1Action::None, V1Output::Response);
    case V1Command::SlotDropDelay:
      argument = v1CommandValue(command, "slotdropdelay:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
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
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->airDropPostDelay = value;
      return result(V1Action::ApplyDropDelay, V1Output::Response);
    case V1Command::AirDropPreDelay:
      argument = v1CommandValue(command, "airdroppredelay:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->airDropPreDelay = value;
      return result(V1Action::None, V1Output::Response);
    case V1Command::AirDropSignalDuration:
      argument = v1CommandValue(command, "airdropdsignalduration:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseBoundedInt(argument, setterRange.minimum, setterRange.maximum,
                           &value))
        return invalid(commandType);
      configuration->airDropSignalTime = value;
      return result(V1Action::None, V1Output::Response);
    case V1Command::AutoMotorStandbyTimeout: {
      uint32_t seconds;
      argument = v1CommandValue(command, "automotorstandbytimeout:");
      v1SetterRange(commandType, *configuration, limits, &setterRange);
      if (!parseUint32(argument, setterRange.maximum, &seconds))
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
