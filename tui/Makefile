BINARY    := osync-tui
CMD       := ./cmd/osync-tui
BUILD_DIR := build

# The release version comes from the repository's VERSION file (the single
# source; see scripts/release/prepare.sh), the commit from git. `osync
# --version` prints both. Override either on the command line if needed.
VERSION   ?= $(shell cat ../VERSION 2>/dev/null || echo "dev")
COMMIT    ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
LDFLAGS   := -s -w -X main.version=$(VERSION) -X main.commit=$(COMMIT)

# The same set the release workflow publishes (.github/workflows/release.yml).
PLATFORMS := linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64 windows/arm64

.PHONY: build build-all test lint release clean install uninstall

build:
	go build -ldflags "$(LDFLAGS)" -o $(BINARY) $(CMD)

build-all: clean
	@mkdir -p $(BUILD_DIR)
	@for platform in $(PLATFORMS); do \
		os=$${platform%%/*}; \
		arch=$${platform##*/}; \
		ext=""; \
		if [ "$$os" = "windows" ]; then ext=".exe"; fi; \
		outname=$(BINARY)-$$os-$$arch$$ext; \
		echo "Building $$outname..."; \
		CGO_ENABLED=0 GOOS=$$os GOARCH=$$arch go build -ldflags "$(LDFLAGS)" -o $(BUILD_DIR)/$$outname $(CMD); \
	done

test:
	go test -race ./...

lint:
	golangci-lint run

release: build-all
	@cd $(BUILD_DIR) && sha256sum * > SHA256SUMS
	@echo "Release artifacts in $(BUILD_DIR)/"

clean:
	rm -rf $(BUILD_DIR) $(BINARY)

# Installs the binary plus the short 'osync' alias users actually type.
PREFIX ?= $(HOME)/.local
install: build
	@mkdir -p $(PREFIX)/bin
	install -m 0755 $(BINARY) $(PREFIX)/bin/$(BINARY)
	ln -sf $(PREFIX)/bin/$(BINARY) $(PREFIX)/bin/osync
	@echo "Installed $(PREFIX)/bin/$(BINARY) (alias: osync)"

uninstall:
	rm -f $(PREFIX)/bin/$(BINARY) $(PREFIX)/bin/osync
