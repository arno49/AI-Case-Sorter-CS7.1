#ifndef CS71_COMMAND_PARSER_H
#define CS71_COMMAND_PARSER_H

#include <stddef.h>

const size_t COMMAND_MAX_LENGTH = 40;

class CommandParser {
 public:
  enum Result {
    NoFrame,
    FrameReady,
    FrameOverflow,
    FrameInvalid
  };

  CommandParser();

  Result consume(char byte);
  const char *frame() const;
  size_t length() const;
  void reset();

 private:
  void append(char byte);

  char buffer_[COMMAND_MAX_LENGTH + 1];
  size_t length_;
  bool overflowed_;
  bool invalid_;
  bool pendingCarriageReturn_;
};

class PendingCommand {
 public:
  PendingCommand();

  bool enqueue(const char *frame, size_t length);
  bool available() const;
  const char *frame() const;
  void clear();

 private:
  char buffer_[COMMAND_MAX_LENGTH + 1];
  bool available_;
};

#endif
