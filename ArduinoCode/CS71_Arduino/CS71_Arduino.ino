/// VERSION CS 7.1.260714.1 ///
/// REQUIRES AI SORTER SOFTWARE VERSION 1.1.46 or newer

#include <Wire.h>
#include <SoftwareSerial.h>
#include <string.h>
#include "command_parser.h"
#include "logic.h"

#define FIRMWARE_VERSION "7.1.260714.1"

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
//this seeks to eliminate that by adding a small pause to let the case bounce and settle. 

#define DEBOUNCE_TIMEOUT 300 //default 500. The number of milliseconds without sensor activation (meaning no brass in the feed) required to trigger a debounce pause.

#define DEBOUNCE_PAUSE_TIME 500 //default 500.  Set to 0 to disable. The number of milliseconds to pause to wait for case to settle. 


///END OF USER CONFIGURATIONS ///
///DO NOT EDIT BELOW THIS LINE ///
int cameraLEDLevel = CAMERA_LED_LEVEL; 

int notificationDelay = FEED_CYCLE_NOTIFICATION_DELAY;
bool airDropEnabled = AIR_DROP_ENABLED;
int feedCycleSignalTime = FEED_CYCLE_COMPLETE_SIGNALTIME;
int feedCyclePreDelay = FEED_CYCLE_COMPLETE_PRESIGNALDELAY;
int feedCyclePostDelay = FEED_CYCLE_COMPLETE_POSTDELAY;
int slotDropDelay = SLOT_DROP_DELAY;
int dropDelay =  airDropEnabled ? feedCyclePostDelay : slotDropDelay;

uint32_t autoMotorStandbyTimeout = AUTO_MOTORSTANDBY_TIMEOUT;
uint32_t autoMotorStandbyTimeoutMs = AUTO_MOTORSTANDBY_TIMEOUT * 1000UL;

int feedSpeed = FEED_MOTOR_SPEED; //represents a number between 1-100
int feedSteps = FEED_STEPS;
int feedMotorSpeed = 500;//this is default and calculated at runtime. do not change this value

int sortSpeed = SORT_MOTOR_SPEED; //represents a number between 1-100
int sortSteps = SORTER_CHUTE_SEPERATION;
int sortMotorSpeed = 500;//this is default and calculated at runtime. do not change this value
int homingSteps = 0;

int feedMicroSteps = feedSteps * FEED_MICROSTEPS;

const int feedramp = (ACC_FACTOR + (FEED_MOTOR_SPEED /2)) / FEED_ACC_SLOPE;
const int sortramp = (ACC_FACTOR + (SORT_MOTOR_SPEED /2)) / SORT_ACC_SLOPE;
int feedOverTravelSteps = feedMicroSteps - (FEED_OVERSTEP_THRESHOLD * FEED_MICROSTEPS);
int feedOffsetSteps = FEED_HOMING_OFFSET_STEPS;
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
int qPos1 = 0;
int qPos2 = 0;
int sortStepsToNextPosition = 0;
int sortStepsToNextPositionTracker=0;
bool SortInProgress = false;
bool SortComplete = false;
bool IsSorting = false;
int sortOffsetSteps = SORT_HOMING_OFFSET_STEPS;
int sortHomingOffset = sortOffsetSteps * SORT_MICROSTEPS;
bool IsSortHoming = false;
bool IsSortHomingOffset = false;
int SortHomingOffsetSteps = sortHomingOffset;
int slotDelayCalc = 0;
bool sorterIsHomed = false;

bool IsTestCycle=false;
bool IsSortTestCycle=false;
int testCycleInterval=0;
int testsCompleted=0;
int sortToSlot=0;
unsigned long theTime;
unsigned long timeSinceLastSortMove;
unsigned long timeSinceLastMotorMove;
unsigned long msgResetTimer;

//debounce variables
unsigned long lastTrigger = millis();
int triggerTimeout = DEBOUNCE_TIMEOUT;
int debounceTime= DEBOUNCE_PAUSE_TIME;
bool proxActivated = false;
bool sensorDelay = false;


void setup() {
  Serial.begin(9600);
  Serial.print(F("Ready\n"));
  
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
  jogSorter();
  IsFeedHoming=true;
  IsSortHoming=true;
  msgResetTimer = millis();
}


void loop() {
   checkSerial();
   getProxState();
   runSortMotor();
   onSortComplete();
   scheduleRun();
   checkFeedErrors();
   runFeedMotor();
   homeFeedMotor();
   homeSortMotor();
   onFeedComplete();
   runAux();
   MotorStandByCheck();
}

int FreeMem(){
  extern int __heap_start, *__brkval;
  int v;
  return(int) &v - (__brkval ==0 ? (int) &__heap_start : (int) __brkval);
}

const uint32_t MAX_AVR_INT = 32767UL;

const char *commandValue(const char *command, const char *prefix) {
  const size_t prefixLength = strlen(prefix);
  return strncmp(command, prefix, prefixLength) == 0
             ? command + prefixLength
             : 0;
}

bool parseAvrInt(const char *text, uint32_t minimum, uint32_t maximum,
                 int *value) {
  uint32_t parsed;
  if (!parseUint32(text, maximum, &parsed) || parsed < minimum) {
    return false;
  }
  *value = static_cast<int>(parsed);
  return true;
}

int maximumSortPosition(int stepSeparation) {
  return static_cast<int>(
      MAX_AVR_INT /
      (static_cast<uint32_t>(stepSeparation) * SORT_MICROSTEPS));
}

bool parseSortPosition(const char *text, int *position) {
  const uint32_t maximumPosition = maximumSortPosition(sortSteps);
  return parseAvrInt(text, 0, maximumPosition, position);
}

bool isNumericCommand(const char *command) {
  return (command[0] >= '0' && command[0] <= '9') ||
         ((command[0] == '-' || command[0] == '+') &&
          command[1] >= '0' && command[1] <= '9');
}

void dispatchCommand(const char *command) {
      int parsedInt;
      const char *value;

      if (isNumericCommand(command)) {
        if (!parseSortPosition(command, &parsedInt)) {
          Serial.print(F("error:invalid slot\n"));
          return;
        }
        moveSorterToNextPosition(parsedInt);
        FeedScheduled = true;
        IsFeeding = false;
        scheduleRun();
        return;
      }

      if (strcmp(command, "version") == 0) {
        Serial.print(FIRMWARE_VERSION);
        Serial.print(F("\n"));
        return;
      }

      if (strcmp(command, "homefeeder") == 0) {
        feedDelayMS=400;
        IsFeedHoming=true;
        Serial.print(F("ok\n"));
        return;
      } 
      if (strcmp(command, "homesorter") == 0) {
        sortDelayMS=400;
        jogSorter();
        qPos1 = 0;
        qPos2 = 0;
        IsSortHoming=true;
        Serial.print(F("ok\n"));
        return;
      } 
      if (strcmp(command, "stop") == 0) {
        FeedScheduled=false;
        IsFeedHoming=false;
        IsFeedHomingOffset = false;
        IsSortHomingOffset = false;
        FeedCycleComplete=true;
        FeedCycleInProgress = false;
        return;
      } 

      value = commandValue(command, "sortto:");
      if (value != 0) {
        if (!parseSortPosition(value, &parsedInt)) {
          Serial.print(F("error:invalid sortto\n"));
          return;
        }
        moveSorterToPosition(parsedInt);
        Serial.print(F("ok\n"));
        return;
      } 

      value = commandValue(command, "xf:");
      if (value != 0) {
        if (!parseSortPosition(value, &parsedInt)) {
          Serial.print(F("error:invalid xf\n"));
          return;
        }
        forceFeed = true;
        moveSorterToNextPosition(parsedInt);
        FeedScheduled = true;
        IsFeeding = false;
        scheduleRun();
        return;
      }

      if (strcmp(command, "getconfig") == 0) {
     
        Serial.print(F("{\"FeedMotorSpeed\":"));
        Serial.print(feedSpeed);

        Serial.print(F(",\"FeedCycleSteps\":"));
        Serial.print(feedSteps);

        Serial.print(F(",\"SortMotorSpeed\":"));
        Serial.print(sortSpeed);

        Serial.print(F(",\"SortSteps\":"));
        Serial.print(sortSteps);

        Serial.print(F(",\"NotificationDelay\":"));
        Serial.print(notificationDelay);

        Serial.print(F(",\"SlotDropDelay\":"));
        Serial.print(slotDropDelay);

        Serial.print(F(",\"AirDropEnabled\":"));
        Serial.print(airDropEnabled);

        Serial.print(F(",\"AirDropPostDelay\":"));
        Serial.print(feedCyclePostDelay);

        Serial.print(F(",\"AirDropPreDelay\":"));
        Serial.print(feedCyclePreDelay);

        Serial.print(F(",\"AirDropSignalTime\":"));
        Serial.print(feedCycleSignalTime);

        Serial.print(F(",\"FeedHomingOffset\":"));
        Serial.print(feedOffsetSteps);
        
         Serial.print(F(",\"SortHomingOffset\":"));
        Serial.print(sortOffsetSteps);

        Serial.print(F(",\"AutoMotorStandbyTimeout\":"));
        Serial.print(autoMotorStandbyTimeout);
        
        Serial.print(F(",\"DebounceTimeout\":"));
        Serial.print(triggerTimeout);

        Serial.print(F(",\"DebouncePauseTime\":"));
        Serial.print(debounceTime);

        #if UseArduinoPWMDimmer == true 
                Serial.print(F(",\"CameraLEDLevel\":"));
                Serial.print(cameraLEDLevel);
        #endif
   
        Serial.print(F("}\n"));
        return;      
      }

      value = commandValue(command, "debounceTimeout:");
      if (value != 0) {
          if (!parseAvrInt(value, 0, MAX_AVR_INT, &parsedInt)) {
            Serial.print(F("error:invalid debounceTimeout\n"));
            return;
          }
          triggerTimeout = parsedInt;
          Serial.print(F("ok\n"));
          return;
      }

      value = commandValue(command, "debounceTime:");
      if (value != 0) {
          if (!parseAvrInt(value, 0, MAX_AVR_INT, &parsedInt)) {
            Serial.print(F("error:invalid debounceTime\n"));
            return;
          }
          debounceTime = parsedInt;
          Serial.print(F("ok\n"));
          return;
      }

       //set feed speed. Values 1-100. Def 60
      value = commandValue(command, "feedspeed:");
      if (value != 0) {
        if (!parseAvrInt(value, 1, 100, &parsedInt)) {
          Serial.print(F("error:invalid feedspeed\n"));
          return;
        }
        feedSpeed = parsedInt;
        setFeedMotorSpeed(feedSpeed);
        Serial.print(F("ok\n"));
        return;
      }
      //set feed homing offset
      value = commandValue(command, "feedhomingoffset:");
      if (value != 0) {
        if (!parseAvrInt(value, 0, SORT_FULL_REVOLUTION_STEPS, &parsedInt)) {
          Serial.print(F("error:invalid feedhomingoffset\n"));
          return;
        }
        feedOffsetSteps = parsedInt;
        feedHomingOffset = feedOffsetSteps * FEED_MICROSTEPS; //48
        FeedHomingOffsetSteps = feedHomingOffset; //48

        Serial.print(F("ok\n"));
        return;
      }

      value = commandValue(command, "sorthomingoffset:");
      if (value != 0) {
        if (!parseAvrInt(value, 0, SORT_FULL_REVOLUTION_STEPS, &parsedInt)) {
          Serial.print(F("error:invalid sorthomingoffset\n"));
          return;
        }
        sortOffsetSteps = parsedInt;
        sortHomingOffset = sortOffsetSteps * SORT_MICROSTEPS; //48
        SortHomingOffsetSteps = sortHomingOffset; //48

        Serial.print(F("ok\n"));
        return;
      }

      value = commandValue(command, "sortspeed:");
      if (value != 0) {
        if (!parseAvrInt(value, 1, 100, &parsedInt)) {
          Serial.print(F("error:invalid sortspeed\n"));
          return;
        }
        sortSpeed = parsedInt;
        setSorterMotorSpeed(sortSpeed);
        Serial.print(F("ok\n"));
        return;
      }

      //set sort steps. Values 1-100. Def 20
      value = commandValue(command, "sortsteps:");
      if (value != 0) {
        if (!parseAvrInt(value, 1, 100, &parsedInt)) {
          Serial.print(F("error:invalid sortsteps\n"));
          return;
        }
        const int maximumPosition = maximumSortPosition(parsedInt);
        if (qPos1 > maximumPosition || qPos2 > maximumPosition) {
          Serial.print(F("error:invalid sortsteps\n"));
          return;
        }
        sortSteps = parsedInt;
        Serial.print(F("ok\n"));
        return;
      }

      //set feed steps. Values 1-1000. Def 100
      value = commandValue(command, "feedsteps:");
      if (value != 0) {
        if (!parseAvrInt(value, 1, 1000, &parsedInt)) {
          Serial.print(F("error:invalid feedsteps\n"));
          return;
        }
        feedSteps = parsedInt;
        feedMicroSteps = feedSteps * FEED_MICROSTEPS;
        feedOverTravelSteps = feedMicroSteps - (FEED_OVERSTEP_THRESHOLD * FEED_MICROSTEPS);
        Serial.print(F("ok\n"));
        return;
      }
      value = commandValue(command, "notificationdelay:");
      if (value != 0) {
        if (!parseAvrInt(value, 0, MAX_AVR_INT, &parsedInt)) {
          Serial.print(F("error:invalid notificationdelay\n"));
          return;
        }
        notificationDelay = parsedInt;
        Serial.print(F("ok\n"));
        return;
      }
      value = commandValue(command, "slotdropdelay:");
      if (value != 0) {
        if (!parseAvrInt(value, 0, MAX_AVR_INT, &parsedInt)) {
          Serial.print(F("error:invalid slotdropdelay\n"));
          return;
        }
        slotDropDelay = parsedInt;
        dropDelay =  airDropEnabled ? feedCyclePostDelay: slotDropDelay;
        Serial.print(F("ok\n"));
        return;
      }
      value = commandValue(command, "airdropenabled:");
      if (value != 0) {
        bool parsedBool;
        if (!parseBool(value, &parsedBool)) {
          Serial.print(F("error:invalid airdropenabled\n"));
          return;
        }
        airDropEnabled = parsedBool;
        dropDelay =  airDropEnabled ? feedCyclePostDelay: slotDropDelay;
        Serial.print(F("ok\n"));
        return;
      }

      value = commandValue(command, "airdroppostdelay:");
      if (value != 0) {
        if (!parseAvrInt(value, 0, MAX_AVR_INT, &parsedInt)) {
          Serial.print(F("error:invalid airdroppostdelay\n"));
          return;
        }
        feedCyclePostDelay = parsedInt;
        dropDelay =  airDropEnabled ? feedCyclePostDelay: slotDropDelay;
        Serial.print(F("ok\n"));
        return;
      }
      value = commandValue(command, "airdroppredelay:");
      if (value != 0) {
        if (!parseAvrInt(value, 0, MAX_AVR_INT, &parsedInt)) {
          Serial.print(F("error:invalid airdroppredelay\n"));
          return;
        }
        feedCyclePreDelay = parsedInt;
        Serial.print(F("ok\n"));
        return;
      }
      value = commandValue(command, "airdropdsignalduration:");
      if (value != 0) {
        if (!parseAvrInt(value, 0, MAX_AVR_INT, &parsedInt)) {
          Serial.print(F("error:invalid airdropdsignalduration\n"));
          return;
        }
        feedCycleSignalTime = parsedInt;
        Serial.print(F("ok\n"));
        return;
      }

      value = commandValue(command, "automotorstandbytimeout:");
      if (value != 0) {
        uint32_t timeoutSeconds;
        uint32_t timeoutMilliseconds;
        if (!parseUint32(value, MAX_STANDBY_TIMEOUT_SECONDS, &timeoutSeconds) ||
            !secondsToMilliseconds(timeoutSeconds, &timeoutMilliseconds)) {
          Serial.print(F("error:invalid automotorstandbytimeout\n"));
          return;
        }
        autoMotorStandbyTimeout = timeoutSeconds;
        autoMotorStandbyTimeoutMs = timeoutMilliseconds;
        Serial.print(F("ok\n"));
        return;
      }
      value = commandValue(command, "cameraledlevel:");
      if (value != 0) {
         int32_t parsedLevel;
         if (!parseInt32(value, INT32_MIN, INT32_MAX, &parsedLevel)) {
           Serial.print(F("error:invalid cameraledlevel\n"));
           return;
         }
         adjustCameraLED(parsedLevel);
         //Serial.print(F("LED: "));
         //Serial.print((float)cameraLEDLevel/255.0*100.0);
         //Serial.print(F("%\n"));
         Serial.print(F("ok\n"));
         return;
       }

      value = commandValue(command, "test:");
      if (value != 0) {
        if (!parseAvrInt(value, 0, MAX_AVR_INT, &parsedInt)) {
          Serial.print(F("error:invalid test\n"));
          return;
        }
        IsTestCycle=true;
        testCycleInterval=parsedInt;
        testsCompleted=0;
        FeedScheduled=false;
        FeedCycleInProgress=false;
        Serial.print(F("testing started\n"));
        return;
      }

      value = commandValue(command, "sorttest:");
      if (value != 0) {
        if (!parseAvrInt(value, 0, MAX_AVR_INT, &parsedInt)) {
          Serial.print(F("error:invalid sorttest\n"));
          return;
        }
        IsSortTestCycle=true;
        testCycleInterval=parsedInt;
        testsCompleted=0;
        Serial.print(F("testing started\n"));
        return;
      }

      if (strcmp(command, "ping") == 0) {
        //Serial.print(FreeMem());
        Serial.print(F(" ok\n"));
        return;
      }
      Serial.print(F("ok\n"));
}

void checkSerial(){
  if(FeedCycleInProgress || SortInProgress){
    return;
  }

  while (Serial.available() > 0 &&
         FeedCycleInProgress == false && SortInProgress == false) {
    const CommandParser::Result result =
        commandParser.consume(static_cast<char>(Serial.read()));
    if (result == CommandParser::FrameOverflow) {
      Serial.print(F("error:command too long\n"));
    } else if (result == CommandParser::FrameInvalid) {
      Serial.print(F("error:invalid command\n"));
    } else if (result == CommandParser::FrameReady) {
      dispatchCommand(commandParser.frame());
      commandParser.reset();
    }
  }
}

//this method is to run all "other" routines not in the main duty cycles (such as tests)
void runAux(){

  //This runs the feed and sort test if scheduled
  if(IsTestCycle==true&&FeedScheduled==false&&FeedCycleInProgress==false){
    if(testsCompleted<testCycleInterval){
       int slot = random(0,6);
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
       int slot = random(0,8);
       delay(40);
       Serial.print(testsCompleted);
       Serial.print(F(" - Sorting to: "));
       Serial.println(slot);
       moveSorterToPosition(slot);
        testsCompleted++;
    }else{
      moveSorterToPosition(0);
      Serial.println("Sort Test Completed");
      IsSortTestCycle=false;
      testsCompleted=0;
      testCycleInterval=0;
    }
  }
}


void moveSorterToNextPosition(int position){
    sortToSlot=position;
    sortStepsToNextPosition = (qPos1 * sortSteps * SORT_MICROSTEPS) - (qPos2 * sortSteps * SORT_MICROSTEPS);
    sortStepsToNextPositionTracker = sortStepsToNextPosition;
    if(sortStepsToNextPosition !=0){
      theTime = millis();
       slotDelayCalc = (dropDelay - (theTime - timeSinceLastSortMove));
       slotDelayCalc = slotDelayCalc > 0? slotDelayCalc : 1;
       if(slotDelayCalc > dropDelay){
         slotDelayCalc=dropDelay;
       }

      // Serial.println(slotDelayCalc);
      delay(slotDelayCalc);
    }
    qPos1 = qPos2;
    qPos2 =position;
    SortInProgress = true;
    SortComplete = false;
    IsSorting = true;
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
 if(FeedCycleComplete == false && FeedSteps < feedOverTravelSteps){
      FeedScheduled=false;
      FeedCycleComplete=true;
      IsFeeding=false;
      IsFeedHoming= false;
      IsFeedHomingOffset=false;
      IsFeedError = true;
      FeedCycleInProgress = false;
      Serial.println("error:feed overtravel detected");
 }
}
void onFeedComplete(){
  if(FeedCycleComplete==true&& IsFeedError==false){
   
    timeSinceLastMotorMove = millis();
   //this allows some time for the brass to start dropping before generating the airblast

    if(airDropEnabled)
    {
      delay(feedCyclePreDelay);
      digitalWrite(FEED_DONE_SIGNAL, HIGH);
      delay(feedCycleSignalTime);
      digitalWrite(FEED_DONE_SIGNAL,LOW);
     
    }
    delay(notificationDelay);
    Serial.print(F("done\n"));
    //Serial.flush();
    FeedCycleComplete=false;
    forceFeed= false;
    return;
  }
  
}

void scheduleRun(){
 
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
          Serial.println("waiting for brass");
         
          msgResetTimer = millis();
      }
    // Serial.flush();
     
    }
  }
}


void getProxState(){

  //if the sensor is triggered, update the last trigger time and set the variable proxActivated
  if(digitalRead(FEED_SENSOR) == FEEDSENSOR_TYPE){
      proxActivated=true;
      lastTrigger = millis();
      return;
  }

  //sensor is not triggered, set the offTimer and set the variable to false
  proxActivated=false;
    
  //check to see if the time since last trigger is longer than the timeout, if so set the delay variable. 
  if(millis() - lastTrigger > triggerTimeout){
    sensorDelay = true;
  }
}

bool readyToFeed()
{
 //if feedsensor is not enabled, or it is a forcefeed,  we are always ready!
  if(FEEDSENSOR_ENABLED==false || forceFeed==true){
    return true;
  }

  //if no brass is detected, we are not ready
  if(proxActivated == false){
    return false;
  }

  //sensorDelay is calcualted in the getProxState() state method above. 
  if(sensorDelay){
        delay(debounceTime);
        sensorDelay = false;
        return false;
  }
   return true;
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
      IsFeedHoming=false;
       IsFeedHomingOffset = false;
      FeedCycleComplete=true;
      FeedCycleInProgress = false;
      return;
    }
    
    if (digitalRead(FEED_HOMING_SENSOR) == FEED_HOMING_SENSOR_TYPE) {
      IsFeedHoming=false;
      if(FeedCycleInProgress){ //if we are homing initially, we don't need to apply offsets
          IsFeedHomingOffset = true;
          FeedHomingOffsetSteps = feedHomingOffset;
      }
      return;
    }
    stepFeedMotor();
    FeedSteps--;
    return;
  }

  if(IsFeedHomingOffset == true){
    if(feedHomingOffset == 0)
    {
      IsFeedHomingOffset = false;
      FeedCycleComplete=true;
      FeedCycleInProgress = false;
      return;
    }
    if(IsFeedHomingOffset == true && FeedHomingOffsetSteps > 0){
      stepFeedMotor();
      FeedHomingOffsetSteps--;
      FeedSteps--;
    }
    else if(IsFeedHomingOffset == true && FeedHomingOffsetSteps<=0){
      IsFeedHomingOffset = false;
      FeedCycleComplete=true;
      FeedCycleInProgress = false;
    }
  }
}

void homeSortMotor(){
  if(IsSortHoming==true && SORT_HOMING_ENABLED == false){
     IsSorting=false;
         SortComplete = true;
         IsSortHoming =false;
         IsSortHomingOffset = false;
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
        IsSortHomingOffset = false;
        IsSortHoming=false;
        SortComplete=true;
        return;
      }
      if(SortHomingOffsetSteps > 0){
        stepSortMotor(true);
        SortHomingOffsetSteps--;
      }
      else if(IsSortHomingOffset == true && SortHomingOffsetSteps<=0){
        IsSortHomingOffset = false;
        IsSortHoming=false;
        SortComplete=true;
        homingSteps=0;
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
  if(SortInProgress || IsFeeding)
    return;
  
  if(autoMotorStandbyTimeout==0)
    return;

  theTime = millis();

  if(hasElapsed(theTime, timeSinceLastMotorMove, autoMotorStandbyTimeoutMs))
     digitalWrite(MOTOR_Enable, HIGH);
}
int js=0;
int jogSteps = 25 * SORT_MICROSTEPS;
void jogSorter(){
    for(js=0;js<jogSteps;js++){
      stepSortMotor(false);
    }
}
void adjustCameraLED(int32_t level)
 {
   cameraLEDLevel = clampByte(level);
   analogWrite(CAMERA_LED_PWM, cameraLEDLevel);
 }
