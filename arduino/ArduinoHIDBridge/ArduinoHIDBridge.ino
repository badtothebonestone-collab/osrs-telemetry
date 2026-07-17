#include <Keyboard.h>
#include <Mouse.h>

const unsigned long BAUD_RATE = 115200;
const unsigned long WATCHDOG_MS = 1000;
const int MAX_MOVE_DELTA = 20;
const unsigned long MAX_HOLD_MS = 250;
const unsigned long MAX_CAMERA_HOLD_MS = 600;
const int MAX_WHEEL_STEP = 3;
const char *PROTOCOL = "arduino_hid.v2";
const char *VERSION = "2.0.0";

static_assert(MAX_CAMERA_HOLD_MS < WATCHDOG_MS, "camera hold must remain below watchdog");

bool armed = false;
String sessionToken = "";
unsigned long lastCommandMillis = 0;
uint8_t mouseButtonsDown = 0;
char keysDown[16];
uint8_t keyCount = 0;

const uint8_t MOUSE_LEFT_BIT = 1;
const uint8_t MOUSE_RIGHT_BIT = 2;
const uint8_t MOUSE_MIDDLE_BIT = 4;

void clearTrackers()
{
  keyCount = 0;
  for (uint8_t i = 0; i < 16; i++)
  {
    keysDown[i] = 0;
  }
  mouseButtonsDown = 0;
}

void releaseAllInput()
{
  Keyboard.releaseAll();
  Mouse.release(MOUSE_LEFT);
  Mouse.release(MOUSE_RIGHT);
  Mouse.release(MOUSE_MIDDLE);
  clearTrackers();
}

void stopAll()
{
  releaseAllInput();
  armed = false;
  sessionToken = "";
}

void ok(const String &message)
{
  Serial.print(F("OK "));
  Serial.println(message);
}

void err(const String &code, const String &command)
{
  releaseAllInput();
  Serial.print(F("ERR "));
  Serial.print(code);
  if (command.length() > 0)
  {
    Serial.print(F(" "));
    Serial.print(command);
  }
  Serial.println();
}

void fatalErr(const String &code, const String &command)
{
  stopAll();
  Serial.print(F("ERR "));
  Serial.print(code);
  if (command.length() > 0)
  {
    Serial.print(F(" "));
    Serial.print(command);
  }
  Serial.println();
}

String tokenAt(const String &line, int index)
{
  int current = 0;
  int start = 0;
  int len = line.length();
  while (start < len)
  {
    while (start < len && line[start] == ' ')
    {
      start++;
    }
    int end = start;
    while (end < len && line[end] != ' ')
    {
      end++;
    }
    if (end > start)
    {
      if (current == index)
      {
        return line.substring(start, end);
      }
      current++;
    }
    start = end + 1;
  }
  return "";
}

long parseLongToken(const String &line, int index, bool &okValue)
{
  String token = tokenAt(line, index);
  if (token.length() == 0)
  {
    okValue = false;
    return 0;
  }
  char *endPtr = NULL;
  long value = strtol(token.c_str(), &endPtr, 10);
  okValue = endPtr != token.c_str() && *endPtr == '\0';
  return value;
}

int tokenCount(const String &line)
{
  int count = 0;
  int start = 0;
  int len = line.length();
  while (start < len)
  {
    while (start < len && line[start] == ' ')
    {
      start++;
    }
    if (start >= len)
    {
      break;
    }
    count++;
    while (start < len && line[start] != ' ')
    {
      start++;
    }
  }
  return count;
}

int buttonCode(const String &button)
{
  if (button == "left")
  {
    return MOUSE_LEFT;
  }
  if (button == "right")
  {
    return MOUSE_RIGHT;
  }
  if (button == "middle")
  {
    return MOUSE_MIDDLE;
  }
  return 0;
}

uint8_t buttonBit(const String &button)
{
  if (button == "left")
  {
    return MOUSE_LEFT_BIT;
  }
  if (button == "right")
  {
    return MOUSE_RIGHT_BIT;
  }
  if (button == "middle")
  {
    return MOUSE_MIDDLE_BIT;
  }
  return 0;
}

char keyCode(const String &name)
{
  if (name.length() == 1)
  {
    return name[0];
  }
  if (name == "left")
  {
    return KEY_LEFT_ARROW;
  }
  if (name == "right")
  {
    return KEY_RIGHT_ARROW;
  }
  if (name == "up")
  {
    return KEY_UP_ARROW;
  }
  if (name == "down")
  {
    return KEY_DOWN_ARROW;
  }
  if (name == "enter")
  {
    return KEY_RETURN;
  }
  if (name == "esc" || name == "escape")
  {
    return KEY_ESC;
  }
  if (name == "space")
  {
    return ' ';
  }
  return 0;
}

char cameraKeyCode(const String &direction)
{
  if (direction == "left")
  {
    return KEY_LEFT_ARROW;
  }
  if (direction == "right")
  {
    return KEY_RIGHT_ARROW;
  }
  if (direction == "up")
  {
    return KEY_UP_ARROW;
  }
  if (direction == "down")
  {
    return KEY_DOWN_ARROW;
  }
  return 0;
}

void trackKeyDown(char key)
{
  for (uint8_t i = 0; i < keyCount; i++)
  {
    if (keysDown[i] == key)
    {
      return;
    }
  }
  if (keyCount < 16)
  {
    keysDown[keyCount++] = key;
  }
}

void trackKeyUp(char key)
{
  uint8_t out = 0;
  for (uint8_t i = 0; i < keyCount; i++)
  {
    if (keysDown[i] != key)
    {
      keysDown[out++] = keysDown[i];
    }
  }
  keyCount = out;
}

bool requireArmed(const String &command)
{
  if (!armed)
  {
    err("NOT_ARMED", command);
    return false;
  }
  return true;
}

long clampMove(long value)
{
  if (value > MAX_MOVE_DELTA)
  {
    return MAX_MOVE_DELTA;
  }
  if (value < -MAX_MOVE_DELTA)
  {
    return -MAX_MOVE_DELTA;
  }
  return value;
}

unsigned long clampHold(unsigned long value)
{
  if (value > MAX_HOLD_MS)
  {
    return MAX_HOLD_MS;
  }
  return value;
}

void sendStatus()
{
  unsigned long age = millis() - lastCommandMillis;
  Serial.print(F("OK STATUS armed="));
  Serial.print(armed ? 1 : 0);
  Serial.print(F(" keysDown="));
  Serial.print(keyCount);
  Serial.print(F(" mouseButtonsDown="));
  Serial.print(mouseButtonsDown ? 1 : 0);
  Serial.print(F(" lastCommandAgeMs="));
  Serial.print(age);
  Serial.print(F(" watchdogMs="));
  Serial.println(WATCHDOG_MS);
}

void handleMove(const String &line, const String &command)
{
  if (!requireArmed(command))
  {
    return;
  }
  bool okX = false;
  bool okY = false;
  long dx = parseLongToken(line, 1, okX);
  long dy = parseLongToken(line, 2, okY);
  if (!okX || !okY)
  {
    err("BAD_ARGS", command);
    return;
  }
  if (abs(dx) > MAX_MOVE_DELTA || abs(dy) > MAX_MOVE_DELTA)
  {
    err("ERR_LIMIT", command);
    return;
  }
  Mouse.move((int)clampMove(dx), (int)clampMove(dy), 0);
  ok("MOVE");
}

void handleMouseDown(const String &line, const String &command)
{
  if (!requireArmed(command))
  {
    return;
  }
  String button = tokenAt(line, 1);
  int code = buttonCode(button);
  uint8_t bit = buttonBit(button);
  if (!code || !bit)
  {
    err("BAD_ARGS", command);
    return;
  }
  Mouse.press(code);
  mouseButtonsDown |= bit;
  ok("MOUSE_DOWN");
}

void handleMouseUp(const String &line, const String &command)
{
  if (!requireArmed(command))
  {
    return;
  }
  String button = tokenAt(line, 1);
  int code = buttonCode(button);
  uint8_t bit = buttonBit(button);
  if (!code || !bit)
  {
    err("BAD_ARGS", command);
    return;
  }
  Mouse.release(code);
  mouseButtonsDown &= ~bit;
  ok("MOUSE_UP");
}

void handleClick(const String &line, const String &command)
{
  if (!requireArmed(command))
  {
    return;
  }
  String button = tokenAt(line, 1);
  int code = buttonCode(button);
  uint8_t bit = buttonBit(button);
  bool okHold = false;
  unsigned long holdMs = (unsigned long)parseLongToken(line, 2, okHold);
  if (!code || !bit || !okHold)
  {
    err("BAD_ARGS", command);
    return;
  }
  holdMs = clampHold(holdMs);
  Mouse.press(code);
  mouseButtonsDown |= bit;
  delay(holdMs);
  Mouse.release(code);
  mouseButtonsDown &= ~bit;
  ok("CLICK");
}

void handleKeyDown(const String &line, const String &command)
{
  if (!requireArmed(command))
  {
    return;
  }
  char code = keyCode(tokenAt(line, 1));
  if (!code)
  {
    err("BAD_ARGS", command);
    return;
  }
  Keyboard.press(code);
  trackKeyDown(code);
  ok("KEY_DOWN");
}

void handleKeyUp(const String &line, const String &command)
{
  if (!requireArmed(command))
  {
    return;
  }
  char code = keyCode(tokenAt(line, 1));
  if (!code)
  {
    err("BAD_ARGS", command);
    return;
  }
  Keyboard.release(code);
  trackKeyUp(code);
  ok("KEY_UP");
}

void handleKeyPress(const String &line, const String &command)
{
  if (!requireArmed(command))
  {
    return;
  }
  char code = keyCode(tokenAt(line, 1));
  bool okHold = false;
  unsigned long holdMs = (unsigned long)parseLongToken(line, 2, okHold);
  if (!code || !okHold)
  {
    err("BAD_ARGS", command);
    return;
  }
  holdMs = clampHold(holdMs);
  Keyboard.press(code);
  trackKeyDown(code);
  delay(holdMs);
  Keyboard.release(code);
  trackKeyUp(code);
  ok("KEY_PRESS");
}

void handleHoldKeys(const String &line, const String &command)
{
  if (!requireArmed(command))
  {
    return;
  }
  String keys = tokenAt(line, 1);
  bool okHold = false;
  unsigned long holdMs = (unsigned long)parseLongToken(line, 2, okHold);
  if (keys.length() == 0 || !okHold)
  {
    err("BAD_ARGS", command);
    return;
  }
  holdMs = clampHold(holdMs);
  uint8_t pressed[8];
  uint8_t pressedCount = 0;
  int start = 0;
  while (start <= keys.length() && pressedCount < 8)
  {
    int comma = keys.indexOf(',', start);
    if (comma < 0)
    {
      comma = keys.length();
    }
    String keyName = keys.substring(start, comma);
    char code = keyCode(keyName);
    if (!code)
    {
      releaseAllInput();
      err("BAD_ARGS", command);
      return;
    }
    Keyboard.press(code);
    trackKeyDown(code);
    pressed[pressedCount++] = code;
    start = comma + 1;
  }
  delay(holdMs);
  for (uint8_t i = 0; i < pressedCount; i++)
  {
    Keyboard.release(pressed[i]);
    trackKeyUp(pressed[i]);
  }
  ok("HOLD_KEYS");
}

void handleCameraHold(const String &line, const String &command)
{
  if (!armed)
  {
    fatalErr("NOT_ARMED", command);
    return;
  }
  if (tokenCount(line) != 3)
  {
    fatalErr("BAD_ARGS", command);
    return;
  }

  String direction = tokenAt(line, 1);
  char code = cameraKeyCode(direction);
  if (!code)
  {
    fatalErr("UNSUPPORTED_KEY", command);
    return;
  }

  bool okDuration = false;
  long requestedDurationMs = parseLongToken(line, 2, okDuration);
  if (!okDuration)
  {
    fatalErr("BAD_ARGS", command);
    return;
  }
  if (requestedDurationMs <= 0 || requestedDurationMs > (long)MAX_CAMERA_HOLD_MS)
  {
    fatalErr("ERR_LIMIT", command);
    return;
  }

  Keyboard.press(code);
  trackKeyDown(code);
  delay((unsigned long)requestedDurationMs);
  Keyboard.release(code);
  trackKeyUp(code);

  Serial.print(F("OK CAMERA_HOLD direction="));
  Serial.print(direction);
  Serial.print(F(" requestedDurationMs="));
  Serial.print(requestedDurationMs);
  Serial.print(F(" appliedDurationMs="));
  Serial.println(requestedDurationMs);
}

void handleWheel(const String &line, const String &command)
{
  if (!armed)
  {
    fatalErr("NOT_ARMED", command);
    return;
  }
  if (tokenCount(line) != 2)
  {
    fatalErr("BAD_ARGS", command);
    return;
  }

  bool okAmount = false;
  long requestedAmount = parseLongToken(line, 1, okAmount);
  if (!okAmount)
  {
    fatalErr("BAD_ARGS", command);
    return;
  }
  if (requestedAmount == 0 || requestedAmount < -MAX_WHEEL_STEP || requestedAmount > MAX_WHEEL_STEP)
  {
    fatalErr("ERR_LIMIT", command);
    return;
  }

  Mouse.move(0, 0, (signed char)requestedAmount);
  Serial.print(F("OK WHEEL requestedAmount="));
  Serial.print(requestedAmount);
  Serial.print(F(" appliedAmount="));
  Serial.println(requestedAmount);
}

void sendCapabilities()
{
  Serial.println(F("OK CAPS schema=input_capabilities.v2 protocol=arduino_hid.v2 firmwareVersion=2.0.0 pointer=1 mouse=1 relativeMove=1 maxMoveDelta=20 buttons=left,right,middle buttonDownUp=1 click=1 maxClickHoldMs=250 keyboard=1 keys=basic keyPress=1 maxKeyPressMs=250 holdKeys=1 maxHoldKeysMs=250 cameraKeyHold=1 cameraKeys=left,right,up,down maxCameraHoldMs=600 wheel=1 maxWheelStep=3 arm=1 watchdog=1 watchdogMs=1000 stopAll=1 disarm=1 status=1 resetSafe=1"));
}

void handleLine(String line)
{
  line.trim();
  if (line.length() == 0)
  {
    return;
  }
  String command = tokenAt(line, 0);
  command.toUpperCase();
  lastCommandMillis = millis();
  if (command == "PING")
  {
    ok("PONG");
  }
  else if (command == "IDENTIFY")
  {
    ok("IDENTIFY name=ArduinoHIDBridge version=" + String(VERSION) + " board=leonardo protocol=" + String(PROTOCOL));
  }
  else if (command == "CAPS")
  {
    sendCapabilities();
  }
  else if (command == "STATUS")
  {
    sendStatus();
  }
  else if (command == "ARM")
  {
    String token = tokenAt(line, 1);
    if (token.length() == 0)
    {
      err("BAD_ARGS", command);
      return;
    }
    releaseAllInput();
    sessionToken = token;
    armed = true;
    ok("ARMED");
  }
  else if (command == "DISARM")
  {
    stopAll();
    ok("DISARMED");
  }
  else if (command == "STOP_ALL")
  {
    stopAll();
    ok("STOP_ALL");
  }
  else if (command == "MOVE")
  {
    handleMove(line, command);
  }
  else if (command == "MOUSE_DOWN")
  {
    handleMouseDown(line, command);
  }
  else if (command == "MOUSE_UP")
  {
    handleMouseUp(line, command);
  }
  else if (command == "CLICK")
  {
    handleClick(line, command);
  }
  else if (command == "KEY_DOWN")
  {
    handleKeyDown(line, command);
  }
  else if (command == "KEY_UP")
  {
    handleKeyUp(line, command);
  }
  else if (command == "KEY_PRESS")
  {
    handleKeyPress(line, command);
  }
  else if (command == "HOLD_KEYS")
  {
    handleHoldKeys(line, command);
  }
  else if (command == "CAMERA_HOLD")
  {
    handleCameraHold(line, command);
  }
  else if (command == "WHEEL")
  {
    handleWheel(line, command);
  }
  else
  {
    fatalErr("UNKNOWN", command);
  }
}

void setup()
{
  armed = false;
  sessionToken = "";
  Keyboard.begin();
  Mouse.begin();
  releaseAllInput();
  Serial.begin(BAUD_RATE);
  lastCommandMillis = millis();
  unsigned long start = millis();
  while (!Serial && millis() - start < 1500)
  {
  }
  ok("BOOT armed=0 released=1 protocol=" + String(PROTOCOL));
}

void loop()
{
  if (armed && millis() - lastCommandMillis > WATCHDOG_MS)
  {
    stopAll();
    ok("WATCHDOG_STOP armed=0 released=1");
  }
  if (Serial.available() > 0)
  {
    String line = Serial.readStringUntil('\n');
    handleLine(line);
  }
}
