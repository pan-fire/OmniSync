# osync in a scratch image (the release binaries are the main way to get it).
# Base image pinned by tag and digest (the toolchain in go.mod); Dependabot
# moves both.
# The builder runs on the build machine's platform and cross-compiles for the
# target (TARGETOS/TARGETARCH from BuildKit), so a multi-platform build needs
# no emulation.

# Build stage
FROM --platform=$BUILDPLATFORM golang:1.27.1-alpine@sha256:8a5910f31396cd4d89662f56c68b3ae31d374308270a1c3bd96672ee5ed43414 AS builder

# hadolint ignore=DL3018
RUN apk add --no-cache git

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download

COPY . .

ARG VERSION=dev
ARG COMMIT=unknown
ARG TARGETOS
ARG TARGETARCH

RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath \
    -ldflags="-s -w -X main.version=${VERSION} -X main.commit=${COMMIT}" \
    -o /osync-tui \
    ./cmd/osync-tui

# Runtime stage
FROM scratch
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /osync-tui /osync-tui
ENTRYPOINT ["/osync-tui"]
