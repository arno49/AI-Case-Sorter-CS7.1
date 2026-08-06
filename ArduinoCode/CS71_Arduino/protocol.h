#ifndef CS71_PROTOCOL_H
#define CS71_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#include "machine_state.h"

#ifndef PROTOCOL_V2_ENABLED
#define PROTOCOL_V2_ENABLED 0
#endif

#define CS71_FIRMWARE_VERSION "7.1.260714.6"

enum class ProtocolMode : uint8_t {
  V1,
#if PROTOCOL_V2_ENABLED
  V2
#endif
};

#include "v2_parser.h"

enum class V2EnvelopeStatus : uint8_t {
  Ready,
  BadFrame,
  BadId
};

struct V2RequestEnvelope {
  uint16_t requestId;
  const char *payload;
  size_t payloadLength;
  bool explicitId;
};

V2EnvelopeStatus parseV2RequestEnvelope(const char *frame, size_t length,
                                        V2RequestEnvelope *envelope);

enum class V2BeginResult : uint8_t {
  Started,
  Busy,
  DuplicateId
};

enum class V2StopResult : uint8_t {
  Stopped,
  DuplicateId
};

struct V2StopCancellation {
  V2StopResult result;
  bool activeCancelled;
  uint16_t activeRequestId;
};

class V2RequestLifecycle {
 public:
  V2RequestLifecycle();

  void reset();
  V2BeginResult beginActive(uint16_t requestId);
  V2BeginResult beginReadOnly(uint16_t requestId);
  bool isActive() const;
  uint16_t activeRequestId() const;
  bool isIdlessActive() const;
  bool owns(uint16_t requestId) const;
  bool terminal(uint16_t requestId);
  V2StopCancellation cancel();
  V2StopCancellation cancelForStop(uint16_t stopRequestId);

 private:
  bool active_;
  uint16_t activeRequestId_;
  bool readOnly_;
  uint16_t readOnlyRequestId_;
};

class RawStopLineDetector {
 public:
  RawStopLineDetector();

  bool consume(char byte);
  void reset();

 private:
  uint8_t state_;
};

enum class V2ResponseKind : uint8_t {
  Accepted,
  Progress,
  Data,
  Done,
  Error
};

enum class V2InspectionCommand : uint8_t {
  None,
  ProtocolVersion,
  Capabilities,
  Status,
  Queue
};

enum class MachinePhase : uint8_t {
  Idle,
  FeedWait,
  FeedMove,
  FeedHome,
  SortMove,
  SortHome,
  Settling,
  AirDrop,
  Diagnostic
};

struct V2MachineActivity {
  bool feedScheduled;
  bool feeding;
  bool feedHoming;
  bool feedHomingOffset;
  bool feedCompletionActive;
  bool airDropActive;
  bool sortMoving;
  bool sortHoming;
  bool sortHomingOffset;
  bool sorterJogActive;
  bool slotDropGateActive;
  bool feedCycleInProgress;
  bool feedCycleComplete;
  bool feedError;
  bool diagnosticActive;
};

struct V2CapabilitiesSnapshot {
  uint32_t slotMax;
  uint32_t slotCount;
  bool pwm;
  bool airDrop;
  bool feedSensor;
  bool feedHome;
  bool sortHome;
};

struct V2StatusSnapshot {
  MachineMode mode;
  MachinePhase phase;
  bool feedHomed;
  bool sortHomed;
  bool motorEnabled;
  bool hasActiveRequest;
  uint16_t activeRequestId;
  uint32_t faultCode;
  uint32_t queuePrevious;
  uint32_t queueNext;
  uint32_t configGeneration;
};

struct V2ObservabilitySnapshot {
  V2CapabilitiesSnapshot capabilities;
  V2StatusSnapshot status;
};

#if !defined(ARDUINO)
struct V2OutputWriter {
  typedef bool (*LineWriter)(void *context, const char *line, size_t length);

  void *context;
  LineWriter writeLine;
};

bool formatV2Response(char *buffer, size_t capacity, uint16_t requestId,
                      V2ResponseKind kind, const char *detail);
bool formatV2Event(char *buffer, size_t capacity, uint16_t sequence,
                   const char *detail);
bool emitV2Response(const V2OutputWriter &writer, uint16_t requestId,
                    V2ResponseKind kind, const char *detail);
bool emitV2Terminal(V2RequestLifecycle *lifecycle,
                    const V2OutputWriter &writer, uint16_t requestId,
                    V2ResponseKind kind, const char *detail);
#endif
V2InspectionCommand classifyV2InspectionCommand(const char *payload,
                                                size_t length);
MachinePhase deriveMachinePhase(const V2MachineActivity &activity);
#if !defined(ARDUINO)
bool streamV2InspectionFields(V2InspectionCommand command,
                              const V2ObservabilitySnapshot &snapshot,
                              const V2OutputWriter &writer,
                              uint16_t requestId);
bool emitV2Inspection(V2RequestLifecycle *lifecycle,
                      const V2OutputWriter &writer, uint16_t requestId,
                      V2InspectionCommand command,
                      const V2ObservabilitySnapshot &snapshot);
#endif

enum class V2NegotiationAction : uint8_t {
  NotHandled,
  Discovery,
  Activate,
  Busy
};

enum class V2Protocol1Action : uint8_t {
  NotHandled,
  ReturnToV1,
  Busy
};

V2NegotiationAction dispatchV2Negotiation(const char *command, size_t length,
                                          bool executionBusy,
                                          bool pendingCommand);
V2Protocol1Action dispatchV2Protocol1(const char *command, size_t length,
                                       bool executionBusy,
                                       bool pendingCommand,
                                       uint16_t *requestId);
const char *v2DiscoveryResponse();
const char *v2ActivationResponse();
#if !defined(ARDUINO)
size_t formatV2Protocol1Response(char *buffer, size_t capacity,
                                 uint16_t requestId, bool busy);
#endif

class ProtocolSession {
 public:
  ProtocolSession();

  ProtocolMode mode() const;
  void reset();

#if PROTOCOL_V2_ENABLED
  void enterV2();
  uint16_t activeRequestId() const;
  uint16_t eventSequence() const;
  uint16_t nextEventSequence();
  bool crcEnabled() const;
  V2FrameParser &parser();
  V2RequestLifecycle &lifecycle();
#if !defined(ARDUINO)
  bool emitEvent(const V2OutputWriter &writer, const char *detail);
#endif
#endif

 private:
  ProtocolMode mode_;
#if PROTOCOL_V2_ENABLED
  uint16_t eventSequence_;
  bool crcEnabled_;
  V2FrameParser parser_;
  V2RequestLifecycle lifecycle_;
#endif
};

#define CS71_V1_RESPONSE_LIST(X)                                            \
  X(Ready, "Ready\n")                                                        \
  X(Ok, "ok\n")                                                              \
  X(Ping, " ok\n")                                                           \
  X(Stopped, "stopped\n")                                                    \
  X(Done, "done\n")                                                          \
  X(TestingStarted, "testing started\n")                                    \
  X(WaitingForBrass, "waiting for brass\n")                                 \
  X(FeedOvertravel, "error:feed overtravel detected\n")                     \
  X(CommandTooLong, "error:command too long\n")                             \
  X(InvalidCommand, "error:invalid command\n")                              \
  X(NotHomed, "error:not homed\n")                                          \
  X(Busy, "error:busy\n")                                                    \
  X(InvalidSlot, "error:invalid slot\n")                                    \
  X(InvalidSortto, "error:invalid sortto\n")                                \
  X(InvalidXf, "error:invalid xf\n")                                        \
  X(InvalidDebounceTimeout, "error:invalid debounceTimeout\n")              \
  X(InvalidDebounceTime, "error:invalid debounceTime\n")                    \
  X(InvalidFeedspeed, "error:invalid feedspeed\n")                          \
  X(InvalidFeedHomingOffset, "error:invalid feedhomingoffset\n")            \
  X(InvalidSortHomingOffset, "error:invalid sorthomingoffset\n")            \
  X(InvalidSortspeed, "error:invalid sortspeed\n")                          \
  X(InvalidSortsteps, "error:invalid sortsteps\n")                          \
  X(InvalidSlotcount, "error:invalid slotcount\n")                          \
  X(InvalidFeedsteps, "error:invalid feedsteps\n")                          \
  X(InvalidNotificationDelay, "error:invalid notificationdelay\n")          \
  X(InvalidSlotDropDelay, "error:invalid slotdropdelay\n")                  \
  X(InvalidAirdropEnabled, "error:invalid airdropenabled\n")                \
  X(InvalidAirdropPostDelay, "error:invalid airdroppostdelay\n")            \
  X(InvalidAirdropPreDelay, "error:invalid airdroppredelay\n")              \
  X(InvalidAirdropSignalDuration, "error:invalid airdropdsignalduration\n") \
  X(InvalidAutoMotorStandbyTimeout, "error:invalid automotorstandbytimeout\n") \
  X(InvalidCameraLedLevel, "error:invalid cameraledlevel\n")                \
  X(InvalidTest, "error:invalid test\n")                                    \
  X(InvalidSorttest, "error:invalid sorttest\n")

enum class V1Response : uint8_t {
#define CS71_V1_RESPONSE_ENUM(name, text) name,
  CS71_V1_RESPONSE_LIST(CS71_V1_RESPONSE_ENUM)
#undef CS71_V1_RESPONSE_ENUM
};

const char *v1ResponseText(V1Response response);

struct ResponseSink {
  typedef void (*V1Emitter)(void *context, V1Response response);

  void *context;
  V1Emitter emitV1;

  void v1(V1Response response) const;

#if PROTOCOL_V2_ENABLED && !defined(ARDUINO)
  typedef void (*V2LineEmitter)(void *context, const char *line);
  V2LineEmitter emitV2Line;
  void v2Line(const char *line) const;
#endif
};

struct Configuration {
  int feedSpeed;
  int feedSteps;
  int sortSpeed;
  int sortSteps;
  uint32_t slotCount;
  int notificationDelay;
  int slotDropDelay;
  bool airDropEnabled;
  int airDropPostDelay;
  int airDropPreDelay;
  int airDropSignalTime;
  int feedHomingOffset;
  int sortHomingOffset;
  uint32_t autoMotorStandbyTimeout;
  uint32_t debounceTimeout;
  uint32_t debouncePauseTime;
  int32_t cameraLedLevel;
};

struct V1DispatchLimits {
  uint32_t maxAvrInt;
  uint32_t maxStandbyTimeoutSeconds;
  uint32_t sortFullRevolutionSteps;
  uint32_t sortMicrosteps;
};

struct V1SetterRange {
  uint32_t minimum;
  uint32_t maximum;
};

struct V1DispatchContext {
  bool running;
  bool busy;
  bool pendingCommand;
  int queuedPositionOne;
  int queuedPositionTwo;
};

enum class V1Command : uint8_t {
  Unknown,
  NumericPosition,
  Stop,
  Version,
  HomeFeeder,
  HomeSorter,
  SortTo,
  ForceFeed,
  GetConfig,
  DebounceTimeout,
  DebounceTime,
  FeedSpeed,
  FeedHomingOffset,
  SortHomingOffset,
  SortSpeed,
  SortSteps,
  SlotCount,
  FeedSteps,
  NotificationDelay,
  SlotDropDelay,
  AirDropEnabled,
  AirDropPostDelay,
  AirDropPreDelay,
  AirDropSignalDuration,
  AutoMotorStandbyTimeout,
  CameraLedLevel,
  Test,
  SortTest,
  Ping
};

enum class V1Action : uint8_t {
  None,
  QueuePending,
  Stop,
  HomeFeeder,
  HomeSorter,
  QueueFeed,
  QueueForcedFeed,
  SortTo,
  StartTest,
  StartSortTest,
  ApplyFeedSpeed,
  ApplySortSpeed,
  ApplyFeedHomingOffset,
  ApplySortHomingOffset,
  ApplyFeedSteps,
  ApplyDropDelay,
  ApplyAutoMotorStandbyTimeout,
  ApplyCameraLedLevel
};

enum class V1Output : uint8_t {
  None,
  Response,
  Version,
  Configuration
};

struct V1DispatchResult {
  V1Action action;
  V1Output output;
  V1Response response;
  int32_t value;
};

enum class V1FrameStatus : uint8_t {
  Ready,
  TooLong,
  Invalid
};

#define CS71_V1_CONFIGURATION_TEXT_LIST(X)                                  \
  X(Start, "{\"FeedMotorSpeed\":")                                          \
  X(FeedCycleSteps, ",\"FeedCycleSteps\":")                                 \
  X(SortMotorSpeed, ",\"SortMotorSpeed\":")                                 \
  X(SortSteps, ",\"SortSteps\":")                                           \
  X(NotificationDelay, ",\"NotificationDelay\":")                           \
  X(SlotDropDelay, ",\"SlotDropDelay\":")                                   \
  X(AirDropEnabled, ",\"AirDropEnabled\":")                                 \
  X(AirDropPostDelay, ",\"AirDropPostDelay\":")                             \
  X(AirDropPreDelay, ",\"AirDropPreDelay\":")                               \
  X(AirDropSignalTime, ",\"AirDropSignalTime\":")                           \
  X(FeedHomingOffset, ",\"FeedHomingOffset\":")                             \
  X(SortHomingOffset, ",\"SortHomingOffset\":")                             \
  X(AutoMotorStandbyTimeout, ",\"AutoMotorStandbyTimeout\":")               \
  X(DebounceTimeout, ",\"DebounceTimeout\":")                               \
  X(DebouncePauseTime, ",\"DebouncePauseTime\":")                           \
  X(CameraLedLevel, ",\"CameraLEDLevel\":")                                 \
  X(End, "}\n")

enum class V1ConfigurationText : uint8_t {
#define CS71_V1_CONFIGURATION_TEXT_ENUM(name, text) name,
  CS71_V1_CONFIGURATION_TEXT_LIST(CS71_V1_CONFIGURATION_TEXT_ENUM)
#undef CS71_V1_CONFIGURATION_TEXT_ENUM
};

const char *v1ConfigurationText(V1ConfigurationText text);
const char *v1FirmwareVersion();

struct V1OutputWriter {
  typedef void (*ResponseWriter)(void *context, V1Response response);
  typedef void (*ConfigurationTextWriter)(void *context,
                                           V1ConfigurationText text);
  typedef void (*UnsignedWriter)(void *context, uint32_t value);
  typedef void (*VersionWriter)(void *context);

  void *context;
  ResponseWriter writeResponse;
  ConfigurationTextWriter writeConfigurationText;
  UnsignedWriter writeUnsigned;
  VersionWriter writeVersion;
};

V1Command classifyV1Command(const char *command);
bool v1CommandRequiresHomedPosition(V1Command command);
V1Response v1InvalidResponse(V1Command command);
const char *v1CommandValue(const char *command, const char *prefix);
bool v1CommandIsSetter(V1Command command);
bool v1SetterArgumentIsSyntacticallyComplete(const char *command,
                                             V1Command commandType);
bool v1SetterRange(V1Command command, const Configuration &configuration,
                   const V1DispatchLimits &limits, V1SetterRange *range);

V1DispatchResult dispatchV1Command(const char *command, size_t length,
                                   const V1DispatchContext &context,
                                   Configuration *configuration,
                                   const V1DispatchLimits &limits);
V1DispatchResult dispatchV1Frame(V1FrameStatus status, const char *command,
                                 size_t length,
                                 const V1DispatchContext &context,
                                 Configuration *configuration,
                                 const V1DispatchLimits &limits);
void writeV1Output(const V1DispatchResult &result,
                   const Configuration &configuration, bool includeCameraLevel,
                   const V1OutputWriter &writer);

#if PROTOCOL_V2_ENABLED && !defined(ARDUINO)
bool streamV2ConfigurationFields(const Configuration &configuration,
                                 bool includeCameraLevel,
                                 const V2OutputWriter &writer,
                                 uint16_t requestId);
#endif

#endif
