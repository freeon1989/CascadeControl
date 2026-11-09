# Запасная упаковка. Основной способ — статический бинарник и systemd:
# панель копирует его по SSH и не зависит от реестра образов.
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY . .
RUN CGO_ENABLED=0 go build -ldflags "-s -w" -o /cascade-agent ./cmd/cascade-agent

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
COPY --from=build /cascade-agent /usr/local/bin/cascade-agent
ENTRYPOINT ["/usr/local/bin/cascade-agent"]
