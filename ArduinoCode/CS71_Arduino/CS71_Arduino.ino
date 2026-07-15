/// VERSION CS 7.1.260714.6 ///
/// REQUIRES AI SORTER SOFTWARE VERSION 1.1.46 or newer

#include <Wire.h>
#include <SoftwareSerial.h>
#include <string.h>
#include "command_parser.h"
#include "feed_completion.h"
#include "logic.h"
#include "machine_state.h"
#include "protocol.h"
#include "proximity_settler.h"
#include "runtime_timer.h"
#include "step_sequence.h"

#define FIRMWARE_VERSION CS71_FIRMWARE_VERSION

//PIN CONFIGURATIONS
//ARDUINO UNO WITH 4 MOTOR CONTROLLER
//Stepper controller is set to 16 Microsteps (3 jumpers in place)

#define UseArduinoPWMDimmer false //if you have configured your hardware for to use arduino PWM dimmer for light control, set this to true. See: https://github.com/sjseth/AI-Case-Sorter-CS7.1/tree/main/CommunityContributions/ArduinoCode/ausrobbo/LED%20Control

#if UseArduinoPWMDimmer == false 
  #define FEED_SENSOR 9 //the proximity sensor under the feed wheel 
  #define CAMERA_LED_PWM 13 //NOT USED
#else
  #define FEED_SENSOR 13 //the proximity sensor under the feed wheel 
  #define CAMERA_LED_PWM 9 //the output pin for the digital PWM 
#endif

#define CAMERA_LED_LEVEL  78 //camera brightness if using digital PWM, otherwise ignored 


#define FEED_DIRPIN 5 //maps to the DIRECTION signal for the feed motor
#define FEED_STEPPIN 2 //maps to the PULSE signal for the feed motor

#define FEED_MICROSTEPS 16  //how many microsteps the controller is configured for. 
#define FEED_HOMING_SENSOR 10  //connects to the feed wheel homing sensor
#define FEED_HOMING_SENSOR_TYPE 1 //1=NO (normally open) default switch, 0=NC (normally closed) (optical switches) 
#define FEEDSENSOR_ENABLED true //enabled if feedsensor is installed and working;//this is a proximity sensor under the feed tube which tells us a case has dropped completely
#define FEEDSENSOR_TYPE 0 // NPN = 0, PNP = 1
#define FEED_DONE_SIGNAL 12   // Writes HIGH Signal When Feed is done. Used for mods like AirDrop
#define FEED_HOMING_ENABLED true //enabled feed homing sensor

#define SORT_DIRPIN 6 //maps to the DIRECTION signal for the sorter motor
#define SORT_STEPPIN 3 //maps to the PULSE signal for the sorter motor

#define SORT_MICROSTEPS 16 //how many microsteps the controller is configured for. 
#define SORT_HOMING_SENSOR 11  //connects to the sorter homing sensor
#define SORT_HOMING_SENSOR_TYPE 1 //1=NO (default) normally open, 0=NC (normally closed) (optical switches) 
#define SORT_HOMING_OFFSET_STEPS 0 //additional steps to continue after homing sensor triggered
#define SORT_FULL_REVOLUTION_STEPS 200
#define SORT_HOMING_MARGIN_STEPS 10 //allows for a sensor offset beyond one nominal revolution

#define AIR_DROP_ENABLED false //enables airdrop

#define MOTOR_Enable 8 //maps to the enable pin for the FEED MOTOR (on r3 shield enable is shared by motors)
#define AUTO_MOTORSTANDBY_TIMEOUT 60 // 0 = disabled; The time in seconds to wait after no motor movement before putting motors in standby
static_assert(AUTO_MOTORSTANDBY_TIMEOUT >= 0, "Motor standby timeout cannot be negative");
static_assert(AUTO_MOTORSTANDBY_TIMEOUT <= MAX_STANDBY_TIMEOUT_SECONDS,
              "Motor standby timeout is too large");
//ARDUINO CONFIGURATIONS

//number of steps between chutes. With the 8 and 10 slot attachments, 20 is the default. 
//If you have customized sorter output drops, you will need to change this setting to meet your needs. 
//Note there are 200 steps in 1 revolution of the sorter motor. 
#define SORTER_CHUTE_SEPERATION 20 
// Number of slots exercised by test: and sorttest:. This is independent of
// SORTER_CHUTE_SEPERATION and may also be changed at runtime with slotcount:.
#define SORTER_SLOT_COUNT 8
static_assert(
    isSlotCountRepresentable(SORTER_SLOT_COUNT, SORTER_CHUTE_SEPERATION,
                             SORT_MICROSTEPS),
    "Highest diagnostic slot exceeds AVR int movement range");


#define FEED_HOMING_OFFSET_STEPS 3 //additional steps to continue after homing sensor triggered
#define FEED_STEPS 70  //The amount to travel before starting the homing cycle. Should be less than (80 - FEED_HOMING_OFFSET_STEPS)
#define FEED_OVERSTEP_THRESHOLD 90 //if we have gone this many steps without hitting a homing node, something is wrong. Throw an overstep error

//FEED MOTOR ACCELLERATION SETTINGS (DISABLED BY DEFAULT)
#define FEED_ACC_SLOPE 32  //2 steps * 16 MicroStes

#define FEED_MOTOR_SPEED 90 //range of 1-100
#define ACC_FEED_ENABLED false //enabled or disables feed motor accelleration. 

//SORT MOTOR ACCELLERATION SETTINGS (ENABLED BY DEFAULT)
#define ACC_SORT_ENABLED true
#define SORT_MOTOR_SPEED 90 //range of 1-100
#define SORT_ACC_SLOPE 64 //64 is default - slope this is the number of microsteps to accelerate and deaccellerate in a sort. 
#define ACC_FACTOR 1200 //1200 is default factor
#define SORT_HOMING_ENABLED true
//FEED DELAY SETTINGS

// Used to send signal to add-ons when feed cycle completes (used by airdrop mod). 
// IF NOT USING MODS, SET TO 0. With Airdrop set to 60-100 (length of the airblast)
#define FEED_CYCLE_COMPLETE_SIGNALTIME 50 

// The amount of time to wait after the feed completes before sending the FEED_CYCLE_COMPLETE SIGNAL
// IF NOT USING MODS, SET TO 0. with Airdrop set to 30-50 which allows the brass to start falling before sending the blast of air. 
#define FEED_CYCLE_COMPLETE_PRESIGNALDELAY 30

// Time in milliseconds to wait before sending "done" response to serialport (allows for everything to stop moving before taking the picture): runs after the feed_cycle_complete signal
// With AirDrop mod enabled, it needs about 20-30MS. If airdrop is not enabled, it should be closer to 50-70. 
// If you are getting blurred pictures, increase this value. 
#define FEED_CYCLE_NOTIFICATION_DELAY 90 

//when airdrop is enabled, this value is used instead of SLOT_DROP_DELAY but does the same thing
//Usually can be 100 or lower, increase value if brass not clearing the tube before it moves to next slot. 
#define FEED_CYCLE_COMPLETE_POSTDELAY 0

// number of MS to wait after feedcycle before moving sort arm.
// Prevents slinging brass. 
// This gives time for the brass to clear the sort tube before moving the sort arm. 
#define SLOT_DROP_DELAY 400

//DEBOUNCE is a feature to counteract case bounce which can occur if the machine runs out of brass and a peice of brass drops a distance from
//from the collator to the feeder. It developes speed and bounces of the prox sensor triggering the sensor and bouncing back up to cause a jam. 
//this requires the sensor to remain continuously active while the case settles.

#define DEBOUNCE_TIMEOUT 300 //default 500. The number of milliseconds without sensor activation (meaning no brass in the feed) required to trigger a debounce pause.

#define DEBOUNCE_PAUSE_TIME 500 //default 500. Set to 0 to disable. The number of milliseconds the sensor must remain continuously active.


///END OF USER CONFIGURATIONS ///
///DO NOT EDIT BELOW THIS LINE ///
Configuration configuration = {
    FEED_MOTOR_SPEED,
    FEED_STEPS,
    SORT_MOTOR_SPEED,
    SORTER_CHUTE_SEPERATION,
    SORTER_SLOT_COUNT,
    FEED_CYCLE_NOTIFICATION_DELAY,
    SLOT_DROP_DELAY,
    AIR_DROP_ENABLED,
    FEED_CYCLE_COMPLETE_POSTDELAY,
    FEED_CYCLE_COMPLETE_PRESIGNALDELAY,
    FEED_CYCLE_COMPLETE_SIGNALTIME,
    FEED_HOMING_OFFSET_STEPS,
    SORT_HOMING_OFFSET_STEPS,
    AUTO_MOTORSTANDBY_TIMEOUT,
    DEBOUNCE_TIMEOUT,
    DEBOUNCE_PAUSE_TIME,
    CAMERA_LED_LEVEL};

#define cameraLEDLevel configuration.cameraLedLevel
#define notificationDelay configuration.notificationDelay
#define airDropEnabled configuration.airDropEnabled
#define feedCycleSignalTime configuration.airDropSignalTime
#define feedCyclePreDelay configuration.airDropPreDelay
#define feedCyclePostDelay configuration.airDropPostDelay
#define slotDropDelay configuration.slotDropDelay
#define autoMotorStandbyTimeout configuration.autoMotorStandbyTimeout
#define feedSpeed configuration.feedSpeed
#define feedSteps configuration.feedSteps
#define sortSpeed configuration.sortSpeed
#define sortSteps configuration.sortSteps
#define slotCount configuration.slotCount
#define feedOffsetSteps configuration.feedHomingOffset
#define sortOffsetSteps configuration.sortHomingOffset
#define triggerTimeout configuration.debounceTimeout
#define debounceTime configuration.debouncePauseTime

int dropDelay =  airDropEnabled ? feedCyclePostDelay : slotDropDelay;

uint32_t autoMotorStandbyTimeoutMs = AUTO_MOTORSTANDBY_TIMEOUT * 1000UL;

int feedMotorSpeed = 500;//this is default and calculated at runtime. do not change this value

int sortMotorSpeed = 500;//this is default and calculated at runtime. do not change this value
int homingSteps = 0;

int feedMicroSteps = feedSteps * FEED_MICROSTEPS;

const int feedramp = (ACC_FACTOR + (FEED_MOTOR_SPEED /2)) / FEED_ACC_SLOPE;
const int sortramp = (ACC_FACTOR + (SORT_MOTOR_SPEED /2)) / SORT_ACC_SLOPE;
int feedOverTravelSteps = feedMicroSteps - (FEED_OVERSTEP_THRESHOLD * FEED_MICROSTEPS);
int feedHomingOffset = feedOffsetSteps * FEED_MICROSTEPS;

bool FeedScheduled = false;
bool IsFeeding = false;
bool IsFeedHoming = false;
bool IsFeedHomingOffset = false;
bool FeedCycleInProgress = false;
bool FeedCycleComplete = false;
bool IsFeedError = false;
int FeedSteps = feedMicroSteps;
int FeedHomingOffsetSteps = feedHomingOffset;
int feedDelayMS = 150;
int sortDelayMS = 400;

bool forceFeed=false;
CommandParser commandParser;
FeedCompletion feedCompletion;
MachineState machineState;
ProtocolSession protocolSession;
PendingCommand pendingCommand;
ProximitySettler proximitySettler;
int qPos1 = 0;
int qPos2 = 0;
int sortStepsToNextPosition = 0;
int sortStepsToNextPositionTracker=0;
bool SortInProgress = false;
bool SortComplete = false;
bool IsSorting = false;
int sortHomingOffset = sortOffsetSteps * SORT_MICROSTEPS;
bool IsSortHoming = false;
bool IsSortHomingOffset = false;
int SortHomingOffsetSteps = sortHomingOffset;
RuntimeTimer slotDropGate;
int pendingQueuedDestination = 0;
StepSequence sorterJog;
bool sorterJogStartsFeedHoming = false;

bool IsTestCycle=false;
bool IsSortTestCycle=false;
RuntimeTimer sortTestPacing;
const uint32_t SORT_TEST_PACING_MS = 40;
int testCycleInterval=0;
int testsCompleted=0;
int sortToSlot=0;
unsigned long theTime;
unsigned long timeSinceLastSortMove;
unsigned long timeSinceLastMotorMove;
unsigned long msgResetTimer;

bool proxActivated = false;

void emitV1Response(void *, V1Response response) {
  switch (response) {
#define CS71_EMIT_V1_RESPONSE(name, text) \
  case V1Response::name:                  \
    Serial.print(F(text));                 \
    return;
    CS71_V1_RESPONSE_LIST(CS71_EMIT_V1_RESPONSE)
#undef CS71_EMIT_V1_RESPONSE
  }
}

#if PROTOCOL_V2_ENABLED
ResponseSink responseSink = {0, emitV1Response, 0};
#else
ResponseSink responseSink = {0, emitV1Response};
#endif

void emitV1ConfigurationText(void *, V1ConfigurationText text) {
  switch (text) {
#define CS71_EMIT_V1_CONFIGURATION_TEXT(name, value) \
  case V1ConfigurationText::name:                    \
    Serial.print(F(value));                           \
    return;
    CS71_V1_CONFIGURATION_TEXT_LIST(CS71_EMIT_V1_CONFIGURATION_TEXT)
#undef CS71_EMIT_V1_CONFIGURATION_TEXT
  }
}

void emitV1Unsigned(void *, uint32_t value) {
  Serial.print(value);
}

void emitV1Version(void *) {
  Serial.print(F(FIRMWARE_VERSION));
  Serial.print(F("\n"));
}

V1OutputWriter v1OutputWriter = {
    0, emitV1Response, emitV1ConfigurationText, emitV1Unsigned, emitV1Version};
V1DispatchLimits v1DispatchLimits = {
    MAX_AVR_INT, MAX_STANDBY_TIMEOUT_SECONDS, SORT_FULL_REVOLUTION_STEPS,
    SORT_MICROSTEPS};

bool executionBusy() {
  return FeedScheduled || IsFeeding || IsFeedHoming || IsFeedHomingOffset ||
         FeedCycleInProgress || (FeedCycleComplete && !IsFeedError) ||
         feedCompletion.isActive() || SortInProgress || slotDropGate.isActive() ||
         IsSorting || IsSortHoming || IsSortHomingOffset ||
         sorterJog.isActive() || IsTestCycle || IsSortTestCycle;
}

void clearPendingCommand() {
  pendingCommand.clear();
}

void updateRecoveryCompletion(MachineAxis axis) {
  if (!machineState.axis(axis).recoveryInProgress) {
    return;
  }

  if (machineState.completeRecovery(axis) &&
      machineState.queueResetRequired()) {
    qPos1 = 0;
    qPos2 = 0;
    machineState.acknowledgeQueueReset();
  }
}

void completeFeedHoming() {
  IsFeedHoming = false;
  IsFeedHomingOffset = false;
  FeedHomingOffsetSteps = 0;
  timeSinceLastMotorMove = millis();
  updateRecoveryCompletion(MachineAxis::Feeder);

  if (FeedCycleInProgress) {
    FeedCycleComplete = true;
    FeedCycleInProgress = false;
  }
}

void completeSorterRecoveryHoming() {
  IsSorting = false;
  IsSortHoming = false;
  IsSortHomingOffset = false;
  SortHomingOffsetSteps = 0;
  homingSteps = 0;
  timeSinceLastMotorMove = millis();
  qPos1 = 0;
  qPos2 = 0;
  updateRecoveryCompletion(MachineAxis::Sorter);
}

void cancelFeedCompletion() {
  feedCompletion.cancel();
  digitalWrite(FEED_DONE_SIGNAL, LOW);
}

void cancelSorterJog() {
  sorterJog.cancel();
  sorterJogStartsFeedHoming = false;
}

void cancelDiagnosticCycles() {
  IsTestCycle = false;
  IsSortTestCycle = false;
  sortTestPacing.cancel();
  testCycleInterval = 0;
  testsCompleted = 0;
}

void enterStoppedState() {
  machineState.enterStopped();
  digitalWrite(MOTOR_Enable, HIGH);
  cancelFeedCompletion();
  cancelSorterJog();

  FeedScheduled = false;
  IsFeeding = false;
  IsFeedHoming = false;
  IsFeedHomingOffset = false;
  FeedCycleInProgress = false;
  FeedCycleComplete = false;
  IsFeedError = false;
  FeedSteps = 0;
  FeedHomingOffsetSteps = 0;

  SortInProgress = false;
  SortComplete = false;
  IsSorting = false;
  IsSortHoming = false;
  IsSortHomingOffset = false;
  sortStepsToNextPosition = 0;
  sortStepsToNextPositionTracker = 0;
  sortToSlot = 0;
  SortHomingOffsetSteps = 0;
  homingSteps = 0;
  slotDropGate.cancel();
  pendingQueuedDestination = 0;

  cancelDiagnosticCycles();
  forceFeed = false;
  proxActivated = false;
  proximitySettler.reset();
  clearPendingCommand();
}


void setup() {
  Serial.begin(9600);
  protocolSession.reset();
  commandParser.reset();
  clearPendingCommand();
  responseSink.v1(V1Response::Ready);
  
  setSorterMotorSpeed(SORT_MOTOR_SPEED);
  setFeedMotorSpeed(FEED_MOTOR_SPEED);


  pinMode(MOTOR_Enable, OUTPUT);
  pinMode(FEED_DIRPIN, OUTPUT);
  pinMode(FEED_STEPPIN, OUTPUT);
  pinMode(SORT_DIRPIN, OUTPUT);
  pinMode(SORT_STEPPIN, OUTPUT);

  pinMode(FEED_DONE_SIGNAL, OUTPUT);
  pinMode(FEED_HOMING_SENSOR, INPUT);
  pinMode(SORT_HOMING_SENSOR, INPUT);
  pinMode(FEED_SENSOR, INPUT_PULLUP);

  #if UseArduinoPWMDimmer == true 
    pinMode(CAMERA_LED_PWM, OUTPUT);
    adjustCameraLED(cameraLEDLevel);
  #endif

  digitalWrite(MOTOR_Enable, LOW);
  digitalWrite(FEED_DIRPIN, LOW);
  jogSorter(true);
  msgResetTimer = millis();
}


void loop() {
   checkSerial();
   getProxState();
   const uint32_t now = millis();
   updateSlotDropGate(now);
   runSortMotor();
   onSortComplete();
   scheduleRun();
   checkFeedErrors();
   runFeedMotor();
   homeFeedMotor();
   homeSortMotor();
   serviceSorterJog();
   onFeedComplete(now);
   runAux();
   MotorStandByCheck();
}

int FreeMem(){
  extern int __heap_start, *__brkval;
  int v;
  return(int) &v - (__brkval ==0 ? (int) &__heap_start : (int) __brkval);
}

void applyV1Action(const V1DispatchResult &result) {
  switch (result.action) {
    case V1Action::Stop:
      enterStoppedState();
      return;
    case V1Action::HomeFeeder:
      digitalWrite(MOTOR_Enable, LOW);
      machineState.beginRecovery(MachineAxis::Feeder);
      feedDelayMS = 400;
      FeedSteps = feedMicroSteps;
      FeedHomingOffsetSteps = 0;
      FeedCycleInProgress = false;
      FeedCycleComplete = false;
      IsFeedError = false;
      IsFeeding = false;
      IsFeedHomingOffset = false;
      IsFeedHoming = true;
      return;
    case V1Action::HomeSorter:
      digitalWrite(MOTOR_Enable, LOW);
      machineState.beginRecovery(MachineAxis::Sorter);
      sortDelayMS = 400;
      SortComplete = false;
      IsSorting = false;
      IsSortHomingOffset = false;
      SortHomingOffsetSteps = 0;
      homingSteps = 0;
      IsSortHoming = false;
      jogSorter(false);
      return;
    case V1Action::QueueFeed:
    case V1Action::QueueForcedFeed:
      forceFeed = result.action == V1Action::QueueForcedFeed;
      moveSorterToNextPosition(result.value);
      FeedScheduled = true;
      IsFeeding = false;
      scheduleRun();
      return;
    case V1Action::SortTo:
      moveSorterToPosition(result.value);
      return;
    case V1Action::StartTest:
      IsTestCycle = true;
      testCycleInterval = result.value;
      testsCompleted = 0;
      FeedScheduled = false;
      FeedCycleInProgress = false;
      return;
    case V1Action::StartSortTest:
      IsSortTestCycle = true;
      sortTestPacing.cancel();
      testCycleInterval = result.value;
      testsCompleted = 0;
      return;
    case V1Action::ApplyFeedSpeed:
      setFeedMotorSpeed(feedSpeed);
      return;
    case V1Action::ApplySortSpeed:
      setSorterMotorSpeed(sortSpeed);
      return;
    case V1Action::ApplyFeedHomingOffset:
      feedHomingOffset = feedOffsetSteps * FEED_MICROSTEPS;
      FeedHomingOffsetSteps = feedHomingOffset;
      return;
    case V1Action::ApplySortHomingOffset:
      sortHomingOffset = sortOffsetSteps * SORT_MICROSTEPS;
      SortHomingOffsetSteps = sortHomingOffset;
      return;
    case V1Action::ApplyFeedSteps:
      feedMicroSteps = feedSteps * FEED_MICROSTEPS;
      feedOverTravelSteps =
          feedMicroSteps - (FEED_OVERSTEP_THRESHOLD * FEED_MICROSTEPS);
      return;
    case V1Action::ApplyDropDelay:
      dropDelay = airDropEnabled ? feedCyclePostDelay : slotDropDelay;
      return;
    case V1Action::ApplyAutoMotorStandbyTimeout:
      autoMotorStandbyTimeoutMs = autoMotorStandbyTimeout * 1000UL;
      return;
    case V1Action::ApplyCameraLedLevel:
      adjustCameraLED(result.value);
      return;
    default:
      return;
  }
}

void handleV1Frame(V1FrameStatus status, const char *command, size_t length,
                   bool dispatchingPending = false) {
#if PROTOCOL_V2_ENABLED
  if (!dispatchingPending && status == V1FrameStatus::Ready) {
    const V2NegotiationAction negotiation = dispatchV2Negotiation(
        command, length, executionBusy(), pendingCommand.available());
    if (negotiation == V2NegotiationAction::Busy) {
      responseSink.v1(V1Response::Busy);
      return;
    }
    if (negotiation == V2NegotiationAction::Discovery) {
      Serial.print(F("protocol:2 available\n"));
      return;
    }
    if (negotiation == V2NegotiationAction::Activate) {
      Serial.print(F("protocol:2 ready\n"));
      Serial.flush();
      commandParser.reset();
      clearPendingCommand();
      protocolSession.enterV2();
      return;
    }
  }
#endif
  V1DispatchContext context = {
      machineState.isRunning(), executionBusy(),
      pendingCommand.available() && !dispatchingPending, qPos1, qPos2};
  V1DispatchResult result = dispatchV1Frame(
      status, command, length, context, &configuration, v1DispatchLimits);
  if (result.action == V1Action::QueuePending) {
    if (!pendingCommand.enqueue(command, length)) {
      responseSink.v1(V1Response::Busy);
    }
    return;
  }
  applyV1Action(result);
  writeV1Output(result, configuration, UseArduinoPWMDimmer == true,
                v1OutputWriter);
}

void dispatchCommand(const char *command) {
  handleV1Frame(V1FrameStatus::Ready, command, strlen(command));
}

#if PROTOCOL_V2_ENABLED
void handleV2Frame(V1FrameStatus status, const char *command, size_t length) {
  if (status != V1FrameStatus::Ready) {
    return;
  }

  uint16_t requestId;
  const V2Protocol1Action action =
      dispatchV2Protocol1(command, length, executionBusy(),
                          pendingCommand.available(), &requestId);
  if (action == V2Protocol1Action::NotHandled) {
    return;
  }

  char response[32];
  formatV2Protocol1Response(response, sizeof(response), requestId,
                            action == V2Protocol1Action::Busy);
  Serial.print(response);
  if (action == V2Protocol1Action::ReturnToV1) {
    Serial.flush();
    commandParser.reset();
    clearPendingCommand();
    protocolSession.reset();
  }
}
#endif

void checkSerial(){
  while (Serial.available() > 0) {
    const CommandParser::Result result =
        commandParser.consume(static_cast<char>(Serial.read()));
    if (result == CommandParser::FrameOverflow) {
#if PROTOCOL_V2_ENABLED
      if (protocolSession.mode() == ProtocolMode::V2) {
        handleV2Frame(V1FrameStatus::TooLong, 0, 0);
      } else
#endif
      handleV1Frame(V1FrameStatus::TooLong, 0, 0);
    } else if (result == CommandParser::FrameInvalid) {
#if PROTOCOL_V2_ENABLED
      if (protocolSession.mode() == ProtocolMode::V2) {
        handleV2Frame(V1FrameStatus::Invalid, 0, 0);
      } else
#endif
      handleV1Frame(V1FrameStatus::Invalid, 0, 0);
    } else if (result == CommandParser::FrameReady) {
      const char *frame = commandParser.frame();
#if PROTOCOL_V2_ENABLED
      if (protocolSession.mode() == ProtocolMode::V2) {
        handleV2Frame(V1FrameStatus::Ready, frame, commandParser.length());
      } else
#endif
      handleV1Frame(V1FrameStatus::Ready, frame, commandParser.length());
      commandParser.reset();
    }
  }

  if (pendingCommand.available() && !executionBusy()) {
    handleV1Frame(V1FrameStatus::Ready, pendingCommand.frame(),
                  strlen(pendingCommand.frame()), true);
    pendingCommand.clear();
  }
}

//this method is to run all "other" routines not in the main duty cycles (such as tests)
void runAux(){
  if (!machineState.isRunning()) {
    return;
  }
  if (feedCompletion.isActive()) {
    return;
  }

  //This runs the feed and sort test if scheduled
  if(IsTestCycle==true&&FeedScheduled==false&&FeedCycleInProgress==false){
    if(testsCompleted<testCycleInterval){
       int slot = random(0, slotCount);
         moveSorterToNextPosition(slot);
        
        FeedScheduled = true;
        IsFeeding = false;
        scheduleRun();
        
        Serial.print(testsCompleted);
        Serial.print(F(" - "));
        Serial.print(slot);
        Serial.print(F("\n"));
        testsCompleted++;

            
    }else{
      IsTestCycle=false;
      testCycleInterval=0;
      testsCompleted=0;
    }
  }

  //this runs the sorter only test cycles if scheduled
  if(IsSortTestCycle==true&&SortInProgress==false){
    if(testsCompleted<testCycleInterval){
       const uint32_t now = millis();
       if (!sortTestPacing.isActive()) {
         sortTestPacing.start(now, SORT_TEST_PACING_MS);
         return;
       }
       if (!sortTestPacing.hasElapsed(now)) {
         return;
       }
       sortTestPacing.cancel();
       int slot = random(0, slotCount);
       Serial.print(testsCompleted);
       Serial.print(F(" - Sorting to: "));
       Serial.println(slot);
       moveSorterToPosition(slot);
        testsCompleted++;
    }else{
      sortTestPacing.cancel();
      moveSorterToPosition(0);
      Serial.println(F("Sort Test Completed"));
      IsSortTestCycle=false;
      testsCompleted=0;
      testCycleInterval=0;
    }
  }
}


void beginQueuedSorterTransition(int position) {
    sortToSlot=position;
    sortStepsToNextPosition = (qPos1 * sortSteps * SORT_MICROSTEPS) - (qPos2 * sortSteps * SORT_MICROSTEPS);
    sortStepsToNextPositionTracker = sortStepsToNextPosition;
    qPos1 = qPos2;
    qPos2 =position;
    SortInProgress = true;
    SortComplete = false;
    IsSorting = true;
}

void moveSorterToNextPosition(int position){
    const int queuedSortSteps =
       (qPos1 * sortSteps * SORT_MICROSTEPS) -
       (qPos2 * sortSteps * SORT_MICROSTEPS);
    const uint32_t now = millis();
    const uint32_t dropDelayMs = static_cast<uint32_t>(dropDelay);

    if (queuedSortSteps != 0 &&
       !hasElapsed(now, timeSinceLastSortMove, dropDelayMs)) {
      pendingQueuedDestination = position;
      slotDropGate.start(timeSinceLastSortMove, dropDelayMs);
      return;
    }

    beginQueuedSorterTransition(position);
}

void updateSlotDropGate(uint32_t now) {
  if (!slotDropGate.hasElapsed(now)) {
    return;
  }

  const int destination = pendingQueuedDestination;
  pendingQueuedDestination = 0;
  slotDropGate.cancel();
  beginQueuedSorterTransition(destination);
}

void moveSorterToPosition(int position){
    sortToSlot=position;
    sortStepsToNextPosition = (qPos1 * sortSteps * SORT_MICROSTEPS) - (position * sortSteps * SORT_MICROSTEPS);
    sortStepsToNextPositionTracker = sortStepsToNextPosition;
   
  // Serial.println(position);
   //Serial.println(sortStepsToNextPosition);
    qPos1 =position;
    qPos2 =position;
    SortInProgress = true;
    SortComplete = false;
    IsSorting = true;
}

void runSortMotor(){
  if(IsSorting==true){
    
    if(sortStepsToNextPosition==0){
     
      if(qPos1==0){
        homingSteps=0;
         IsSortHoming=true;
      }else{
         IsSorting=false;
         SortComplete = true;
      }
      return;
    }
    setAccSortDelay();
    
    if(sortStepsToNextPosition > 0){
      stepSortMotor(true);
      sortStepsToNextPosition--;
    }
    else {
      stepSortMotor(false);
      sortStepsToNextPosition++;
    }
  }
}
void setAccSortDelay(){
    if(ACC_SORT_ENABLED == false){
      sortDelayMS=sortMotorSpeed;
      return;
    }
    //up ramp
    if((abs(sortStepsToNextPositionTracker) - abs(sortStepsToNextPosition)) < SORT_ACC_SLOPE ){
      sortDelayMS = ACC_FACTOR - ((abs(sortStepsToNextPositionTracker) - abs(sortStepsToNextPosition)) * sortramp);
      if (sortDelayMS < sortMotorSpeed) {
         sortDelayMS = sortMotorSpeed;
      }
     
      return;
    }
  
    //down ramp
    if (abs(sortStepsToNextPositionTracker) - abs(sortStepsToNextPosition) > (abs(sortStepsToNextPositionTracker) - SORT_ACC_SLOPE)){
        sortDelayMS = (SORT_ACC_SLOPE - abs(sortStepsToNextPosition)) * sortramp;
         if (sortDelayMS < sortMotorSpeed) {
             sortDelayMS = sortMotorSpeed;
         }
         if (sortDelayMS > ACC_FACTOR) {
             sortDelayMS = ACC_FACTOR;
         }
         
    }else{
      //normal run speed
      sortDelayMS = sortMotorSpeed;
    }
    
}
void stepSortMotor(bool forward){
     digitalWrite(MOTOR_Enable, LOW);
     if(forward==true){
       digitalWrite(SORT_DIRPIN, HIGH);
     }else{
      digitalWrite(SORT_DIRPIN, LOW);
    }
    digitalWrite(SORT_STEPPIN, HIGH);
    delayMicroseconds(1);  //pulse width
    digitalWrite(SORT_STEPPIN, LOW);
    delayMicroseconds(sortDelayMS); //controls motor speed
}
void onSortComplete(){
  if(SortInProgress==true && SortComplete==true){
        SortInProgress=false;
        timeSinceLastSortMove = millis();
        timeSinceLastMotorMove = timeSinceLastSortMove;
       // Serial.println("runscheduled");
  }
}

void checkFeedErrors(){
  const bool feedSearchActive = IsFeeding || IsFeedHoming;
  if(feedSearchActive && FeedCycleComplete == false &&
     FeedSteps < feedOverTravelSteps){
      const bool sorterWasMoving =
          SortInProgress || IsSorting || IsSortHoming || IsSortHomingOffset ||
          sorterJog.isActive();
      FeedScheduled=false;
      FeedCycleComplete=true;
      IsFeeding=false;
      IsFeedHoming= false;
      IsFeedHomingOffset=false;
      IsFeedError = true;
      FeedCycleInProgress = false;
      forceFeed = false;
      machineState.invalidateAxis(MachineAxis::Feeder);
      SortInProgress = false;
      SortComplete = false;
      IsSorting = false;
      IsSortHoming = false;
      IsSortHomingOffset = false;
      sortStepsToNextPosition = 0;
      sortStepsToNextPositionTracker = 0;
      SortHomingOffsetSteps = 0;
      homingSteps = 0;
      cancelSorterJog();
      if (sorterWasMoving) {
        machineState.invalidateAxis(MachineAxis::Sorter);
      }
      digitalWrite(MOTOR_Enable, HIGH);
      cancelFeedCompletion();
      proximitySettler.reset();
      cancelDiagnosticCycles();
      responseSink.v1(V1Response::FeedOvertravel);
  }
}
void onFeedComplete(uint32_t now){
  if (!machineState.isRunning()) {
    if (feedCompletion.isActive()) {
      cancelFeedCompletion();
      FeedCycleComplete = false;
      forceFeed = false;
    }
    return;
  }

  if (FeedCycleComplete && !IsFeedError && !feedCompletion.isActive()) {
    const FeedCompletionConfig config = {
        airDropEnabled,
        static_cast<uint32_t>(feedCyclePreDelay),
        static_cast<uint32_t>(feedCycleSignalTime),
        static_cast<uint32_t>(notificationDelay)};
    if (feedCompletion.start(now, config)) {
      timeSinceLastMotorMove = now;
    }
  }

  for (uint8_t transitions = 0; transitions < 3; ++transitions) {
    const FeedCompletionEvent event = feedCompletion.update(now);
    if (event == FeedCompletionEvent::None) {
      return;
    }
    if (event == FeedCompletionEvent::SignalHigh) {
      digitalWrite(FEED_DONE_SIGNAL, HIGH);
    } else if (event == FeedCompletionEvent::SignalLow) {
      digitalWrite(FEED_DONE_SIGNAL, LOW);
    } else if (event == FeedCompletionEvent::Notify) {
      responseSink.v1(V1Response::Done);
      FeedCycleComplete = false;
      forceFeed = false;
      return;
    }
  }
}

void scheduleRun(){
  if (slotDropGate.isActive()) {
    return;
  }

  if(FeedScheduled==true && IsFeeding==false){
    if(readyToFeed()){
      //set run variables
      IsFeedError=false;
      FeedSteps = feedMicroSteps;
      FeedScheduled=false;
      FeedCycleInProgress = true;
      FeedCycleComplete=false;
      IsFeeding=true;
     
    }else{
      theTime = millis();
      if(theTime - msgResetTimer > 1000){
         // Serial.flush();
          responseSink.v1(V1Response::WaitingForBrass);
         
          msgResetTimer = millis();
      }
    // Serial.flush();
     
    }
  }
}


void getProxState(){
  const uint32_t now = millis();
  proxActivated = digitalRead(FEED_SENSOR) == FEEDSENSOR_TYPE;
  proximitySettler.observe(now, proxActivated, triggerTimeout);
}

bool readyToFeed()
{
  const bool bypass = FEEDSENSOR_ENABLED == false || forceFeed == true;
  return proximitySettler.ready(millis(), debounceTime, bypass);
}


void runFeedMotor() {
  if(SortInProgress){
    return;
  }

  if(IsFeeding==true && FeedSteps > 0 )
  {
    setAccFeedDelay();
    stepFeedMotor();
    FeedSteps--;
    return;
  }
  if(IsFeeding==true){
    IsFeeding=false;
    IsFeedHoming = true;
  }
  return;

}

void homeFeedMotor(){
  
  if(IsFeedHoming==true ){
   
    if(FEED_HOMING_ENABLED == false){
      completeFeedHoming();
      return;
    }
    
    if (digitalRead(FEED_HOMING_SENSOR) == FEED_HOMING_SENSOR_TYPE) {
      IsFeedHoming=false;
      IsFeedHomingOffset = true;
      FeedHomingOffsetSteps = feedHomingOffset;
    }
    else {
      stepFeedMotor();
      FeedSteps--;
      return;
    }
  }

  if(IsFeedHomingOffset == true){
    if(feedHomingOffset == 0)
    {
      completeFeedHoming();
      return;
    }
    if(IsFeedHomingOffset == true && FeedHomingOffsetSteps > 0){
      stepFeedMotor();
      FeedHomingOffsetSteps--;
      FeedSteps--;
    }
    else if(IsFeedHomingOffset == true && FeedHomingOffsetSteps<=0){
      completeFeedHoming();
    }
  }
}

void homeSortMotor(){
  if(IsSortHoming==true && SORT_HOMING_ENABLED == false){
     if (machineState.axis(MachineAxis::Sorter).recoveryInProgress) {
       completeSorterRecoveryHoming();
     } else {
       IsSorting = false;
       SortComplete = true;
       IsSortHoming = false;
       IsSortHomingOffset = false;
     }
     return;
  }
  if(IsSortHoming==true){
     //if a sort is in progress and the arm is moving from any position to zero
     //this code is reached when the steps have been completed to go to zero
     //we are going to check if the homing sensor is not activated (which it should be as we are at zero), 
     //if not, we are going to step the motor until it is or we have reached 200 steps (1 complete turn)
    if(IsSorting==true){
         if(digitalRead(SORT_HOMING_SENSOR)!=SORT_HOMING_SENSOR_TYPE){ //
          if(homingSteps < (SORT_FULL_REVOLUTION_STEPS*SORT_MICROSTEPS)){
              stepSortMotor(true);  
              homingSteps++; 
          }
          return;
         }
         IsSorting=false;
         SortComplete = true;
         IsSortHoming =false;
         homingSteps=0;
         return;
    }
    //else if we are not doing an offset move (post homing) and the sensor is not 
    //activated, lets keep moving until it is or we hit 210 homing steps (otherwise we turn indefinitely)
    else if(IsSortHomingOffset != true){
      if(digitalRead(SORT_HOMING_SENSOR)!=SORT_HOMING_SENSOR_TYPE){
          if(homingSteps < ((SORT_FULL_REVOLUTION_STEPS + SORT_HOMING_MARGIN_STEPS)*SORT_MICROSTEPS)){
              stepSortMotor(true);  
              homingSteps++;          
          }
      }else{ //we are homed! Time to schedule an offset move and reset the homing steps counter. 
        IsSortHomingOffset=true;
        SortHomingOffsetSteps = sortHomingOffset;
        homingSteps = 0;
      }
    }

   //If sort homing offset true, means we are in offset steps
    if(IsSortHomingOffset == true){
      if(sortHomingOffset == 0) //if there are no offset steps, we are done
      {
        completeSorterRecoveryHoming();
        return;
      }
      if(SortHomingOffsetSteps > 0){
        stepSortMotor(true);
        SortHomingOffsetSteps--;
      }
      else if(IsSortHomingOffset == true && SortHomingOffsetSteps<=0){
        completeSorterRecoveryHoming();
      }
    }
  }


 
}

void stepFeedMotor(){
    digitalWrite(MOTOR_Enable, LOW);
    digitalWrite(FEED_STEPPIN, HIGH);
    delayMicroseconds(1);  //pulse width
    digitalWrite(FEED_STEPPIN, LOW);
    delayMicroseconds(feedDelayMS); //controls motor speed
}

void setAccFeedDelay(){
    if(ACC_FEED_ENABLED == false){
      feedDelayMS=feedMotorSpeed;
      return;
    }
    //up ramp
    if((feedMicroSteps - FeedSteps) < FEED_ACC_SLOPE ){
      feedDelayMS = ACC_FACTOR - ((feedMicroSteps - FeedSteps) * feedramp);
      if (feedDelayMS < feedMotorSpeed) {
         feedDelayMS = feedMotorSpeed;
      }
      //Serial.println(msdelay);
      return;
    }
  
    //down ramp
    if (feedMicroSteps - FeedSteps > (feedMicroSteps - FEED_ACC_SLOPE)){
        feedDelayMS = (FEED_ACC_SLOPE - FeedSteps) * feedramp;
         if (feedDelayMS < feedMotorSpeed) {
             feedDelayMS = feedMotorSpeed;
         }
         if (feedDelayMS > ACC_FACTOR) {
             feedDelayMS = ACC_FACTOR;
         }
         //Serial.println(msdelay);
    }else{
      //normal run speed
      feedDelayMS = feedMotorSpeed;
    }
    
}

void setSorterMotorSpeed(int speed) {
  sortMotorSpeed = convertSpeedToDelay(speed);
}
void setFeedMotorSpeed(int speed) {
  feedMotorSpeed = convertSpeedToDelay(speed);
}

void MotorStandByCheck(){
  if(SortInProgress || IsFeeding || IsFeedHoming || IsFeedHomingOffset ||
     IsSortHoming || IsSortHomingOffset || sorterJog.isActive())
    return;
  
  if(autoMotorStandbyTimeout==0)
    return;

  theTime = millis();

  if(hasElapsed(theTime, timeSinceLastMotorMove, autoMotorStandbyTimeoutMs))
     digitalWrite(MOTOR_Enable, HIGH);
}
void jogSorter(bool startFeedHoming){
  sorterJogStartsFeedHoming = startFeedHoming;
  sorterJog.start(25UL * SORT_MICROSTEPS);
}
void serviceSorterJog(){
  if (sorterJog.isActive()) {
    if (sorterJog.takeStep()) {
      stepSortMotor(false);
    }
  }

  if (!sorterJog.isComplete()) {
    return;
  }

  const bool startFeedHoming = sorterJogStartsFeedHoming;
  cancelSorterJog();
  if (machineState.mode() == MachineMode::Stopped) {
    return;
  }

  IsSortHoming = true;
  if (startFeedHoming) {
    IsFeedHoming = true;
  }
}
void adjustCameraLED(int32_t level)
 {
   cameraLEDLevel = clampByte(level);
   analogWrite(CAMERA_LED_PWM, cameraLEDLevel);
 }
