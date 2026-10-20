CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -std=gnu11
PREFIX ?= /usr/local
CPPFLAGS += -I$(PREFIX)/include -I$(PREFIX)/include/mtllib
LDFLAGS += -L$(PREFIX)/lib64 -Wl,-rpath,$(PREFIX)/lib64
LDLIBS = -lmtlslib -loqs -lcrypto -lm

mtlbench: mtlbench.c
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ $< $(LDFLAGS) $(LDLIBS)

clean:
	rm -f mtlbench

.PHONY: clean
