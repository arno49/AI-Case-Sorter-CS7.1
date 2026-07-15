#ifndef CS71_PROTOCOL_H
#define CS71_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

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

class ProtocolSession {
 public:
  ProtocolSession();

  ProtocolMode mode() const;
  void reset();

#if PROTOCOL_V2_ENABLED
  void setMode(ProtocolMode mode);
#endif

 private:
  ProtocolMode mode_;
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

#if PROTOCOL_V2_ENABLED
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

#endif
