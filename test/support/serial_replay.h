#ifndef CS71_TEST_SERIAL_REPLAY_H
#define CS71_TEST_SERIAL_REPLAY_H

#include <stddef.h>
#include <string.h>

template <size_t Capacity>
class SerialReplay {
 public:
  SerialReplay() : length_(0), overflowed_(false) {
    bytes_[0] = '\0';
  }

  void write(const char *text) {
    if (text == 0) {
      return;
    }
    const size_t incoming = strlen(text);
    if (incoming > Capacity - length_) {
      overflowed_ = true;
      return;
    }
    memcpy(bytes_ + length_, text, incoming + 1);
    length_ += incoming;
  }

  const char *bytes() const {
    return bytes_;
  }

  bool overflowed() const {
    return overflowed_;
  }

  void clear() {
    length_ = 0;
    overflowed_ = false;
    bytes_[0] = '\0';
  }

 private:
  char bytes_[Capacity + 1];
  size_t length_;
  bool overflowed_;
};

#endif
